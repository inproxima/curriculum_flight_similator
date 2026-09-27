import createClient from "openapi-fetch";
import type { paths } from "./schema";

export const api = createClient<paths>({ baseUrl: "" });

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

/** Unwrap an openapi-fetch result, throwing the server's consistent error envelope. */
export function unwrap<T>(r: { data?: T; error?: unknown; response: Response }): T {
  if (r.error !== undefined || !r.response.ok) {
    const env = (r.error as { error?: { code?: string; message?: string; details?: unknown }; detail?: unknown }) ?? {};
    const e = env.error;
    throw new ApiError(
      r.response.status,
      e?.code ?? "http_error",
      e?.message ?? (typeof env.detail === "string" ? env.detail : `Request failed (${r.response.status})`),
      e?.details ?? env.detail,
    );
  }
  return r.data as T;
}

/** For endpoints whose payloads are intentionally loosely typed in OpenAPI (graph views, analyses). */
export async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const body = res.status === 204 ? undefined : await res.json().catch(() => undefined);
  if (!res.ok) {
    const e = body?.error;
    throw new ApiError(res.status, e?.code ?? "http_error", e?.message ?? `Request failed (${res.status})`, e?.details);
  }
  return body as T;
}

export function qs(params: Record<string, string | number | boolean | string[] | undefined | null>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, x));
    else sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}
