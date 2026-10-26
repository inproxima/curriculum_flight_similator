/** The version a program opens on when nothing is remembered: the published one, else the newest draft.
 *  Versions arrive oldest first (the API orders them by creation), so the newest draft is the last one. The
 *  newest draft is where current work (outline imports, AI mapping) happens. */
export function defaultVersion<V extends { status: string }>(versions: V[]): V | undefined {
  return versions.find((v) => v.status === "published") ?? versions[versions.length - 1];
}
