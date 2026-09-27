/**
 * Authentication for the SPA.
 * - Local single-user mode (development): nothing to do.
 * - OIDC (Cognito or institutional IdP): Authorization Code + PKCE via oidc-client-ts. The access token is
 *   attached to same-origin /api/ requests by a fetch wrapper. Tokens live in sessionStorage (cleared when the
 *   tab closes) and are renewed silently before expiry.
 * EventSource cannot send headers, so job streams use a short-lived, job-scoped stream token.
 */
import { UserManager, WebStorageStateStore, type User } from "oidc-client-ts";

export interface AuthConfig { mode: "local_single_user" | "oidc"; issuer?: string; client_id?: string; scopes?: string }

let manager: UserManager | null = null;
let user: User | null = null;
let config: AuthConfig = { mode: "local_single_user" };
const origFetch = window.fetch.bind(window);

function isApi(input: RequestInfo | URL): boolean {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  return url.startsWith("/api/") || url.startsWith(`${window.location.origin}/api/`);
}

export function accessToken(): string | null {
  return user && !user.expired ? user.access_token : null;
}

export function authHeaders(): Record<string, string> {
  const t = accessToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
  if (config.mode === "oidc" && isApi(input)) {
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    const t = accessToken();
    if (t && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${t}`);
    return origFetch(input, { ...init, headers }).then((r) => {
      if (r.status === 401 && manager) void manager.signinRedirect({ state: window.location.pathname });
      return r;
    });
  }
  return origFetch(input, init);
};

export async function initAuth(): Promise<{ config: AuthConfig; user: User | null }> {
  config = await origFetch("/api/v1/auth/config").then((r) => r.json()).catch(() => ({ mode: "local_single_user" }));
  if (config.mode !== "oidc") return { config, user: null };
  manager = new UserManager({
    authority: config.issuer!,
    client_id: config.client_id!,
    redirect_uri: `${window.location.origin}/auth/callback`,
    post_logout_redirect_uri: window.location.origin,
    response_type: "code",
    scope: config.scopes ?? "openid email profile",
    automaticSilentRenew: true,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  });
  manager.events.addUserLoaded((u) => { user = u; });
  manager.events.addUserUnloaded(() => { user = null; });
  if (window.location.pathname === "/auth/callback") {
    const u = await manager.signinRedirectCallback();
    user = u;
    const back = typeof u.state === "string" ? u.state : "/";
    window.history.replaceState({}, "", back);
    return { config, user };
  }
  user = await manager.getUser();
  return { config, user: user && !user.expired ? user : null };
}

export function signIn() {
  return manager?.signinRedirect({ state: window.location.pathname });
}

export function signOut() {
  return manager?.signoutRedirect();
}

/** Open a job progress stream; adds a short-lived stream token when sign-in is required. */
export async function openJobStream(jobId: string): Promise<EventSource> {
  let url = `/api/v1/jobs/${jobId}/stream`;
  if (config.mode === "oidc") {
    const r = await fetch(`/api/v1/jobs/${jobId}/stream-token`, { method: "POST" });
    const { token } = await r.json();
    url += `?st=${encodeURIComponent(token)}`;
  }
  return new EventSource(url);
}

/** Download an API resource (exports) with auth, then open or save it. */
export async function openAuthed(url: string, filename?: string) {
  const r = await fetch(url);
  const blob = await r.blob();
  const obj = URL.createObjectURL(blob);
  if (filename) {
    const a = document.createElement("a");
    a.href = obj;
    a.download = filename;
    a.click();
  } else {
    window.open(obj, "_blank", "noopener");
  }
  setTimeout(() => URL.revokeObjectURL(obj), 60_000);
}
