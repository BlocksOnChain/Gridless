/**
 * Browser smoke test. Drives the real UI against the real backend -- no mocks.
 * Screenshots land in e2e/shots/ so failures are inspectable.
 *
 *   node e2e/smoke.mjs
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE ?? "http://localhost:3000";
const SHOTS = new URL("./shots/", import.meta.url).pathname;
mkdirSync(SHOTS, { recursive: true });

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });

const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));

try {
  // --- landing -----------------------------------------------------------
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.screenshot({ path: `${SHOTS}01-landing.png`, fullPage: true });
  check("landing renders heading", await page.getByRole("heading", { name: "Gridless" }).isVisible());
  const rows = await page.locator("li").filter({ hasText: ".xlsx" }).count();
  check("landing lists workbooks", rows > 0, `${rows} rows`);

  // --- review ------------------------------------------------------------
  // Pick any analysed workbook: the review screen no longer shows
  // relationships, so it no longer matters whether this one has any.
  const API = process.env.API ?? "http://127.0.0.1:8010";
  const wbs = await (await fetch(`${API}/api/workbooks`, { headers: { "X-Org-Id": "1" } })).json();
  let target = null;
  for (const wb of wbs) {
    if (wb.entity_count === 0) continue;
    const p = await (await fetch(`${API}/api/workbooks/${wb.id}/proposal`, {
      headers: { "X-Org-Id": "1" },
    })).json();
    if (p.entities?.length) { target = wb.id; break; }
  }
  check("an analysed workbook exists", target !== null);

  check("review link present", (await page.getByRole("link", { name: "Review" }).count()) > 0);
  await page.goto(`${BASE}/workbooks/${target}/review`, { waitUntil: "networkidle" });
  await page.screenshot({ path: `${SHOTS}02-review.png`, fullPage: true });

  const entityButtons = page.locator('aside button[aria-current]');
  const entityCount = await entityButtons.count();
  check("table list populated", entityCount > 0, `${entityCount} tables`);

  const fieldRows = page.locator("table tbody tr");
  check("column table populated", (await fieldRows.count()) > 0, `${await fieldRows.count()} columns`);

  // The whole point of the redesign: none of the inference's own measurements
  // are on this page any more.
  const jargon = await page.getByText(/confidence|match \d|many_to_one|header row/i).count();
  check("no inference internals on the review screen", jargon === 0, `${jargon} mention(s)`);
  check(
    "relationships are not shown at all",
    (await page.getByRole("button", { name: "Accept" }).count()) === 0 &&
      (await page.getByRole("button", { name: "Reject" }).count()) === 0,
  );
  check("the assistant is on the page", await page.getByRole("button", { name: "Send" }).isVisible());

  // --- switch entity -----------------------------------------------------
  if (entityCount > 1) {
    const before = await fieldRows.count();
    await entityButtons.nth(1).click();
    await page.waitForTimeout(300);
    const after = await page.locator("table tbody tr").count();
    check("switching entity changes fields", after > 0, `${before} -> ${after}`);
    await entityButtons.nth(0).click();
    await page.waitForTimeout(300);
  }

  // --- edit a field name (persists?) -------------------------------------
  const firstName = page.locator("table tbody tr").first().locator("input[type=text], input:not([type])").first();
  const original = await firstName.inputValue();
  const edited = `${original} EDITED`;
  await firstName.fill(edited);
  await firstName.blur();
  await page.waitForTimeout(1200);
  await page.reload({ waitUntil: "networkidle" });
  const afterReload = await page
    .locator("table tbody tr").first()
    .locator("input[type=text], input:not([type])").first().inputValue();
  check("field rename persists across reload", afterReload === edited, `got ${JSON.stringify(afterReload)}`);
  // restore
  const restore = page.locator("table tbody tr").first().locator("input[type=text], input:not([type])").first();
  await restore.fill(original);
  await restore.blur();
  await page.waitForTimeout(1000);

  // --- change a type -----------------------------------------------------
  const typeSelect = page.locator("table tbody tr").first().locator("select").first();
  const originalType = await typeSelect.inputValue();
  const newType = originalType === "text" ? "integer" : "text";
  await typeSelect.selectOption(newType);
  await page.waitForTimeout(1200);
  await page.reload({ waitUntil: "networkidle" });
  const typeAfter = await page.locator("table tbody tr").first().locator("select").first().inputValue();
  check("type change persists", typeAfter === newType, `${originalType} -> ${typeAfter}`);
  await page.locator("table tbody tr").first().locator("select").first().selectOption(originalType);
  await page.waitForTimeout(1000);

  // --- add a column by hand, then remove it ------------------------------
  const columnsBefore = await page.locator("table tbody tr").count();
  await page.getByLabel("New column name").fill("E2E Temp Column");
  await page.getByRole("button", { name: "Add column" }).click();
  await page.waitForTimeout(1200);
  await page.reload({ waitUntil: "networkidle" });
  const columnsAfterAdd = await page.locator("table tbody tr").count();
  check(
    "a hand-added column persists across reload",
    columnsAfterAdd === columnsBefore + 1,
    `${columnsBefore} -> ${columnsAfterAdd}`,
  );
  await page.screenshot({ path: `${SHOTS}03-review-added-column.png`, fullPage: true });

  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Remove E2E Temp Column" }).click();
  await page.waitForTimeout(1200);
  await page.reload({ waitUntil: "networkidle" });
  const columnsAfterRemove = await page.locator("table tbody tr").count();
  check(
    "removing a column persists across reload",
    columnsAfterRemove === columnsBefore,
    `${columnsAfterAdd} -> ${columnsAfterRemove}`,
  );

  // --- upload page -------------------------------------------------------
  await page.goto(`${BASE}/upload`, { waitUntil: "networkidle" });
  await page.screenshot({ path: `${SHOTS}04-upload.png`, fullPage: true });
  check("upload page renders dropzone", await page.getByText(/Drag .xlsx files here/).isVisible());

  const input = page.locator('input[type=file]');
  await input.setInputFiles(new URL("../../backend/fixtures/synthetic/two_tables.xlsx", import.meta.url).pathname);
  await page.waitForTimeout(3000);
  const uploaded = await page.getByText(/uploaded \(id/).count();
  check("upload succeeds via UI", uploaded > 0);

  const errorsBeforeDeliberateFailure = consoleErrors.length;

  // --- bad upload is reported, not swallowed -----------------------------
  const { writeFileSync } = await import("node:fs");
  writeFileSync("/tmp/e2e-bad.xlsx", "definitely not a workbook");
  await input.setInputFiles("/tmp/e2e-bad.xlsx");
  await page.waitForTimeout(2500);
  const badMsg = await page.getByText(/not a readable .xlsx workbook/).count();
  check("bad upload shows a real error", badMsg > 0);
  await page.screenshot({ path: `${SHOTS}05-upload-error.png`, fullPage: true });

  // Only errors logged before the deliberate 400 count as real defects.
  const unexpected = consoleErrors.slice(0, errorsBeforeDeliberateFailure);
  check("no unexpected console errors", unexpected.length === 0, unexpected.slice(0, 3).join(" | "));
} catch (err) {
  check("run completed without throwing", false, String(err).slice(0, 300));
  await page.screenshot({ path: `${SHOTS}99-crash.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
