import { defineConfig, devices } from "@playwright/test";

/** End-to-end tests against a running stack (`make up` + `make seed-synthetic`). Uses SYNTHETIC data only. */
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173", viewport: { width: 1600, height: 1000 }, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 1000 } } }],
});
