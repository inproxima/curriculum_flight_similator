import AxeBuilder from "@axe-core/playwright";
import { type Page } from "@playwright/test";
import { expect, test } from "./fixtures";

/** The Web as a force-directed network (canvas): views, focus on click, the column layout as an alternative.
 *  SYNTHETIC fixture only. Nodes are found through a development-only hook on the canvas container. */

type Hit = { kind: "node" | "link"; id: string } | null;
type Hook = {
  idOf: (k: string) => string | null;
  screenOf: (id: string) => { x: number; y: number } | null;
  settled: () => boolean;
  zoom: () => number;
  pickAt: (x: number, y: number) => Hit;
  list: () => { id: string; key: string; type: string; title: string }[];
  links: () => { id: string; drawn: boolean; lit: boolean; mid: { x: number; y: number } | null }[];
  graph: { centerAt: () => { x: number; y: number } };
};

async function open(page: Page) {
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.goto("/");
  const program = page.getByRole("combobox", { name: "Program" });
  await program.selectOption((await program.locator("option", { hasText: "Synthetic Biomedical Sciences" }).getAttribute("value"))!);
  const version = page.getByRole("combobox", { name: "Curriculum version and cohort" });
  await version.selectOption((await version.locator("option", { hasText: "2024–25 (synthetic) · published" }).getAttribute("value"))!);
  await expect(page.getByRole("region", { name: "Network of the alignment web" })).toBeVisible();
}

const status = (page: Page) => page.locator(".net-status-text");
const inspector = (page: Page) => page.getByRole("complementary", { name: "Inspector" });

/** Wait until the layout has settled and the view has been fitted. */
async function settle(page: Page) {
  await expect.poll(() => page.evaluate(() => (document.querySelector(".net-canvas") as unknown as { __net?: Hook }).__net?.settled() ?? false), { timeout: 20_000 }).toBe(true);
}

/** Where an element is drawn, in page coordinates. */
async function pointOf(page: Page, key: string) {
  const box = (await page.locator(".net-canvas").boundingBox())!;
  const at = await page.evaluate((k) => {
    const net = (document.querySelector(".net-canvas") as unknown as { __net: Hook }).__net;
    const id = net.idOf(k);
    return id ? net.screenOf(id) : null;
  }, key);
  expect(at, `${key} is drawn`).not.toBeNull();
  return { x: box.x + at!.x, y: box.y + at!.y };
}

/** The camera: zoom and the centre of the view. */
const camera = (page: Page) =>
  page.evaluate(() => {
    const net = (document.querySelector(".net-canvas") as unknown as { __net: Hook }).__net;
    return { k: net.zoom(), ...net.graph.centerAt() };
  });

/** One plain click where the element is drawn, as a person would. */
async function clickNode(page: Page, key: string) {
  await settle(page);
  const at = await pointOf(page, key);
  await page.mouse.click(at.x, at.y);
}

/** For each kind of element, one that is drawn well inside the canvas: hover shows its tooltip, a click selects it. */
async function everyKindIsSelectable(page: Page) {
  await settle(page);
  for (const type of ["program_outcome", "course", "course_outcome", "assessment", "topic"]) {
    // read positions afresh: a selection can resize the canvas (the change composer below it grows), which shifts the view
    const box = (await page.locator(".net-canvas").boundingBox())!;
    const n = await page.evaluate(
      ({ type, w, h }) => {
        const net = (document.querySelector(".net-canvas") as unknown as { __net: Hook }).__net;
        for (const n of net.list()) {
          const s = n.type === type ? net.screenOf(n.id) : null;
          if (!s || s.x < 60 || s.y < 60 || s.x > w - 60 || s.y > h - 60) continue;
          const hit = net.pickAt(s.x, s.y);
          if (hit?.kind === "node" && hit.id === n.id) return { key: n.key, title: n.title, x: s.x, y: s.y };
        }
        return null;
      },
      { type, w: box.width, h: box.height },
    );
    expect(n, `a ${type} is drawn inside the canvas`).toBeTruthy();
    await page.mouse.move(box.x + n!.x, box.y + n!.y);
    await expect(page.locator(".net-tooltip .net-tip-key")).toHaveText(n!.key);
    await page.mouse.click(box.x + n!.x, box.y + n!.y);
    await expect(inspector(page).getByRole("heading", { level: 2 })).toContainText(type === "program_outcome" ? n!.title : n!.key);
  }
}

test("the web opens as a network, and each view brightens one family of links", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("button", { name: "Network", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".net-canvas canvas").first()).toBeVisible();
  await expect(status(page)).toContainText("10 program outcomes");
  await expect(status(page)).toContainText(/course outcomes · \d+ assessments · \d+ topics · \d+ links/);

  const show = page.getByRole("group", { name: "Show" });
  await show.getByRole("button", { name: "Assessments → outcomes" }).click();
  await expect(show.getByRole("button", { name: "Assessments → outcomes" })).toHaveAttribute("aria-pressed", "true");
  await expect(status(page)).toContainText(/\d+ courses · \d+ course outcomes · \d+ assessments · \d+ links/);
  await expect(status(page)).not.toContainText("program outcomes");
  await expect(status(page)).not.toContainText("topics");

  await show.getByRole("button", { name: "Outcomes → program outcomes" }).click();
  await expect(status(page)).toContainText(/10 program outcomes · \d+ courses · \d+ course outcomes · \d+ links/);

  // the Layers panel drives the same state: a hand-made combination is "custom"
  await page.getByRole("complementary", { name: "Layers" }).getByRole("checkbox", { name: /Topics/ }).check();
  await expect(show.getByText("custom")).toBeVisible();
  await show.getByRole("button", { name: "Everything" }).click();
  await expect(show.getByText("custom")).toBeHidden();

  await page.getByRole("button", { name: "Legend" }).click();
  await expect(page.locator(".net-legend").getByText("Course outcome")).toBeVisible();
  const axe = await new AxeBuilder({ page }).include(".web-viewbar").include(".net-legend").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(axe.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`)).toEqual([]);
});

test("clicking an element lights up what it connects to and opens it in the Inspector", async ({ page }) => {
  await open(page);
  await clickNode(page, "SYN 202");
  await expect(inspector(page).getByRole("heading", { name: /SYN 202 — Metabolic Biochemistry/ })).toBeVisible();
  await expect(page.locator(".net-status")).toContainText(/SYN 202 lights up \d+ connected elements/);
  await page.getByRole("checkbox", { name: "Only the selection" }).check();
  await expect(page.getByRole("checkbox", { name: "Only the selection" })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(inspector(page).getByRole("heading", { name: "Alignment web" })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "Only the selection" })).toBeHidden();

  // selecting from search focuses the network too
  await page.getByRole("searchbox", { name: "Search" }).fill("SYN 310");
  await page.getByRole("listbox").getByRole("option", { name: /SYN 310 — Critical Appraisal/ }).click();
  await expect(page.locator(".net-status")).toContainText(/SYN 310 lights up \d+ connected elements/);
});

test("the column layout is one click away, and the choice is remembered", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Columns", exact: true }).click();
  await expect(page.locator(".web-node.plo").first()).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Columns", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".web-node.plo").first()).toBeVisible();
  await page.getByRole("button", { name: "Network", exact: true }).click();
  await expect(page.getByRole("region", { name: "Network of the alignment web" })).toBeVisible();
});

test("every kind of element shows its tooltip on hover and is selected by one plain click", async ({ page }) => {
  await open(page);
  await everyKindIsSelectable(page);
});

test("a click that wobbles a little still selects, and clicking another element switches straight to it", async ({ page }) => {
  await open(page);
  await settle(page);
  const a = await pointOf(page, "SYN 202");
  await page.mouse.move(a.x, a.y);
  await page.mouse.down();
  await page.mouse.move(a.x + 2, a.y + 1);
  await page.mouse.up();
  await expect(inspector(page).getByRole("heading", { name: /SYN 202 — / })).toBeVisible();
  const b = await pointOf(page, "SYN 310");
  await page.mouse.click(b.x, b.y);
  await expect(inspector(page).getByRole("heading", { name: /SYN 310 — / })).toBeVisible();
  await expect(page.locator(".net-status")).toContainText(/SYN 310 lights up \d+ connected elements/);
});

test("clicking empty canvas clears the selection, even where a link that is not lit runs", async ({ page }) => {
  await open(page);
  await clickNode(page, "SYN 202");
  await expect(inspector(page).getByRole("heading", { name: /SYN 202 — / })).toBeVisible();
  const box = (await page.locator(".net-canvas").boundingBox())!;
  const spot = await page.evaluate(
    ({ w, h }) => {
      const net = (document.querySelector(".net-canvas") as unknown as { __net: Hook }).__net;
      for (const l of net.links()) {
        if (!l.drawn || l.lit || !l.mid) continue;
        const { x, y } = l.mid;
        if (x > 40 && y > 40 && x < w - 40 && y < h - 40 && net.pickAt(x, y) === null) return { x, y };
      }
      return null;
    },
    { w: box.width, h: box.height },
  );
  expect(spot, "a drawn link that is not lit crosses empty canvas").not.toBeNull();
  await page.mouse.click(box.x + spot!.x, box.y + spot!.y);
  await expect(inspector(page).getByRole("heading", { name: "Alignment web" })).toBeVisible();
  await expect(page.locator(".net-status")).not.toContainText("lights up");
});

test("a click on the canvas leaves the view where it is; picking from search brings the element into view", async ({ page }) => {
  await open(page);
  await settle(page);
  const before = await camera(page);
  const at = await pointOf(page, "SYN 202");
  await page.mouse.click(at.x, at.y);
  await expect(page.locator(".net-status")).toContainText(/SYN 202 lights up/);
  await page.waitForTimeout(900); // longer than any camera animation
  const after = await camera(page);
  expect(after.k).toBeCloseTo(before.k, 5);
  // the element stays under the pointer, even though the change composer below the canvas gained a suggestion
  const still = await pointOf(page, "SYN 202");
  expect(Math.hypot(still.x - at.x, still.y - at.y)).toBeLessThan(1.5);

  await page.getByRole("searchbox", { name: "Search" }).fill("SYN 310");
  await page.getByRole("listbox").getByRole("option", { name: /SYN 310 — Critical Appraisal/ }).click();
  await expect(page.locator(".net-status")).toContainText(/SYN 310 lights up/);
  await expect
    .poll(async () => {
      const c = await camera(page);
      return Math.abs(c.k - after.k) > 0.01 || Math.hypot(c.x - after.x, c.y - after.y) > 1;
    })
    .toBe(true);
});

test("a click right after zooming selects what is under the pointer now", async ({ page }) => {
  await open(page);
  await settle(page);
  const a = await pointOf(page, "SYN 202");
  await page.mouse.move(a.x + 70, a.y + 40);
  await page.mouse.wheel(0, -400); // zoom in around a point beside it, so it moves on screen
  const b = await pointOf(page, "SYN 202");
  expect(Math.hypot(b.x - a.x, b.y - a.y)).toBeGreaterThan(20);
  await page.mouse.click(b.x, b.y);
  await expect(inspector(page).getByRole("heading", { name: /SYN 202 — / })).toBeVisible();
});

test.describe("on a high-density screen", () => {
  test.use({ deviceScaleFactor: 2 });
  test("every kind of element is picked where it is drawn", async ({ page }) => {
    await open(page);
    await everyKindIsSelectable(page);
  });
});
