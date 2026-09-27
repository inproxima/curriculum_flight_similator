"""Text normalization and quote location.

Documented normalization (applied only for search and quote matching; original text is always kept):
  1. Unicode NFKC (folds ligatures such as "ﬁ" → "fi" and full-width forms).
  2. Soft hyphen removal; "word-\\nword" line-break hyphenation joined.
  3. Typographic quotes/dashes mapped to ASCII equivalents.
  4. Whitespace runs collapsed to a single space.
"""

import re
import unicodedata

_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "­": ""})


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).translate(_TRANSLATE)
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)
    return re.sub(r"\s+", " ", t).strip()


def _flex_pattern(quote: str) -> re.Pattern[str]:
    toks = normalize(quote).split(" ")
    parts = []
    for tok in toks:
        # allow typographic variants of ASCII punctuation in the source
        esc = re.escape(tok)
        esc = esc.replace("'", "['‘’]").replace('"', '["“”]').replace("\\-", "[-–—]")
        parts.append(esc)
    return re.compile(r"\s+".join(parts), re.I)


def find_span(text: str, quote: str) -> tuple[int, int] | None:
    """Locate `quote` in original `text` allowing only the documented normalization. Returns offsets in `text`."""
    if not quote or not quote.strip():
        return None
    m = _flex_pattern(quote).search(unicodedata.normalize("NFKC", text))
    if m and len(unicodedata.normalize("NFKC", text)) == len(text):
        return m.start(), m.end()
    if m:  # NFKC changed lengths; fall back to direct search on original
        m2 = _flex_pattern(quote).search(text)
        return (m2.start(), m2.end()) if m2 else None
    return None


def quote_matches(text: str, start: int, end: int, quote: str) -> bool:
    return normalize(text[start:end]).lower() == normalize(quote).lower()
