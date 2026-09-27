"""Controlled URL fetcher for webpage import (SSRF-resistant).

Rules
- http/https only, default ports only, no userinfo.
- Host must match CFS_FETCH_ALLOWED_DOMAINS (suffix match) when configured.
- Every resolved address must be globally routable; private, loopback, link-local, multicast, reserved,
  CGNAT, and cloud-metadata addresses are refused.
- The connection is made to the validated IP (Host header + TLS SNI set to the name) so DNS cannot be
  re-pointed between check and use (rebinding).
- Redirects are not followed automatically; each Location is re-validated (max 3 hops).
- Response size and time are capped; only HTML, PDF, and plain text are accepted.
- HTML is converted to text; scripts/styles are dropped and nothing is executed.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from cfs.core.config import get_settings
from cfs.core.errors import AppError

MAX_REDIRECTS = 3
ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "application/pdf", "text/plain")
METADATA_IPS = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254")}


class FetchRefused(AppError):
    status_code = 422
    code = "fetch_refused"


@dataclass
class Fetched:
    url: str
    final_url: str
    content_type: str
    data: bytes
    redirects: list[str] = field(default_factory=list)
    title: str | None = None


def _check_host(host: str) -> None:
    allowed = [d.strip().lower().lstrip(".") for d in get_settings().fetch_allowed_domains.split(",") if d.strip()]
    h = host.lower().rstrip(".")
    if allowed and not any(h == d or h.endswith("." + d) for d in allowed):
        raise FetchRefused(f"Host '{host}' is not in the allowed domains ({', '.join(allowed)}).")


def _ip_ok(ip: ipaddress._BaseAddress) -> bool:
    if ip in METADATA_IPS:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not (
        ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_unspecified
    )


def resolve_public(host: str, port: int) -> str:
    try:
        literal = ipaddress.ip_address(host)
        addrs = [literal]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise FetchRefused(f"Cannot resolve host '{host}'.") from e
        addrs = [ipaddress.ip_address(i[4][0]) for i in infos]
    if not addrs or not all(_ip_ok(a) for a in addrs):
        raise FetchRefused("The address is not a public internet address; internal addresses are never fetched.")
    return str(addrs[0])


def validate_url(url: str) -> tuple[str, str, int, str]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchRefused("Only http and https URLs can be imported.")
    if parts.username or parts.password:
        raise FetchRefused("URLs with credentials are not allowed.")
    if not parts.hostname:
        raise FetchRefused("URL has no host.")
    default = 443 if parts.scheme == "https" else 80
    if parts.port not in (None, default):
        raise FetchRefused("Only default ports are allowed.")
    _check_host(parts.hostname)
    ip = resolve_public(parts.hostname, default)
    return parts.scheme, parts.hostname, default, ip


def fetch(url: str, *, transport: httpx.BaseTransport | None = None) -> Fetched:
    s = get_settings()
    current, redirects = url, []
    with httpx.Client(timeout=15, follow_redirects=False, transport=transport) as client:
        for _ in range(MAX_REDIRECTS + 1):
            scheme, host, port, ip = validate_url(current)
            parts = urlsplit(current)
            target = f"{scheme}://{ip if ':' not in ip else f'[{ip}]'}{parts.path or '/'}" + (
                f"?{parts.query}" if parts.query else ""
            )
            headers = {
                "Host": host,
                "User-Agent": "CurriculumFlightSimulator/1.0 (+document import)",
                "Accept": "text/html,application/pdf,text/plain",
            }
            ext = {"sni_hostname": host} if scheme == "https" else {}
            with client.stream("GET", target, headers=headers, extensions=ext) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location")
                    if not loc:
                        raise FetchRefused("Redirect without a Location header.")
                    current = urljoin(current, loc)
                    redirects.append(current)
                    continue
                if r.status_code != 200:
                    raise FetchRefused(f"The server returned HTTP {r.status_code}.")
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype not in ALLOWED_TYPES:
                    raise FetchRefused(f"Unsupported content type '{ctype or 'unknown'}'.")
                data = b""
                for chunk in r.iter_bytes():
                    data += chunk
                    if len(data) > s.fetch_max_bytes:
                        raise FetchRefused("The page is larger than the import size limit.")
                return Fetched(url, current, ctype, data, redirects)
    raise FetchRefused("Too many redirects.")


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "nav", "footer"}
    BLOCK = {
        "p",
        "div",
        "section",
        "article",
        "li",
        "tr",
        "br",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "table",
        "ul",
        "ol",
        "header",
        "main",
        "dd",
        "dt",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.title: str | None = None
        self._in_title = False
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
            self.out.append("\n")
        if tag == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title = (self.title or "") + data.strip()
            return
        if self.skip:
            return
        self.out.append(data)
        if self._href and data.strip():
            self.links.append((data.strip(), self._href))
            self._href = None


def html_to_text(html: str, base_url: str) -> tuple[str, str | None]:
    p = _Text()
    p.feed(html)
    lines = [" ".join(line.split()) for line in "".join(p.out).splitlines()]
    text = "\n".join(line for line in lines if line)
    header = (
        f"Webpage import (text only; scripts and navigation removed)\nSource: {base_url}\n"
        + (f"Title: {p.title}\n" if p.title else "")
        + "\n"
    )
    doc_links = [f"- {t}: {urljoin(base_url, h)}" for t, h in p.links if h and h.lower().endswith(".pdf")]
    footer = ("\n\nLinked documents (references only, not imported):\n" + "\n".join(doc_links)) if doc_links else ""
    return header + text + footer, p.title
