import { test as base, expect } from "@playwright/test";

/** Every spec starts with no background AI jobs: the test database holds the backend suite's jobs, whose
 *  notifications would cover the page. A test that exercises the job center routes the list itself (the route
 *  registered last wins). */
export const test = base.extend<{ quietJobs: void }>({
  quietJobs: [
    async ({ page }, use) => {
      await page.route(/\/api\/v1\/jobs\?.*kind=ai_/, (r) => r.fulfill({ json: [] }));
      await use();
    },
    { auto: true },
  ],
});
export { expect };
