import { defaultVersion } from "./defaultVersion";

describe("defaultVersion", () => {
  it("prefers the published version", () => {
    expect(defaultVersion([{ id: "a", status: "published" }, { id: "b", status: "draft" }])?.id).toBe("a");
  });
  it("otherwise opens the newest draft", () => {
    expect(defaultVersion([{ id: "2025", status: "draft" }, { id: "2026", status: "draft" }])?.id).toBe("2026");
  });
  it("handles no versions", () => {
    expect(defaultVersion([])).toBeUndefined();
  });
});
