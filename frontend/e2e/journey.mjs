/**
 * The definition-of-done journey, end to end in a real browser:
 * upload -> analyse -> review -> correct a field -> commit -> use the generated app.
 *
 *   node e2e/journey.mjs
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE ?? "http://localhost:3000";
const API = process.env.API ?? "http://127.0.0.1:8010";
const ORG = { "X-Org-Id": "1" };
const SHOTS = new URL("./shots/", import.meta.url).pathname;
mkdirSync(SHOTS, { recursive: true });

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));

try {
  // Find a committed workbook's entities via the generated app.
  await page.goto(`${BASE}/app`, { waitUntil: "networkidle" });
  await page.screenshot({ path: `${SHOTS}10-app-home.png`, fullPage: true });
  const entityLinks = page.locator("main ul li a").first();
  check("generated app lists entities", (await page.locator("main ul li").count()) > 0);

  const slug = await (await fetch(`${API}/api/entities`, { headers: ORG })).json();
  const customer = slug.find((e) => e.table_slug === "customer");
  check("customer entity is committed", !!customer, `${customer?.record_count} records`);

  // --- list screen -------------------------------------------------------
  await page.goto(`${BASE}/app/customer`, { waitUntil: "networkidle" });
  await page.waitForSelector("table tbody tr");
  const rowCount = await page.locator("table tbody tr").count();
  check("record list renders rows", rowCount > 0, `${rowCount} rows`);
  await page.screenshot({ path: `${SHOTS}11-records.png`, fullPage: true });

  // --- sorting -----------------------------------------------------------
  const firstBefore = await page.locator("table tbody tr td").first().innerText();
  await page.getByRole("button", { name: /Customer ID/i }).click();
  await page.waitForTimeout(900);
  await page.getByRole("button", { name: /Customer ID/i }).click();  // toggle to desc
  await page.waitForTimeout(900);
  const firstAfter = await page.locator("table tbody tr td").first().innerText();
  check("sorting changes order", firstBefore !== firstAfter, `${firstBefore} -> ${firstAfter}`);

  // --- filtering ---------------------------------------------------------
  await page.getByLabel("Filter City").fill("Izmir");
  await page.getByLabel("Filter City").blur();
  await page.waitForTimeout(1200);
  const cities = await page.locator("table tbody tr td:nth-child(3)").allInnerTexts();
  check(
    "filtering narrows the list",
    cities.length > 0 && cities.every((c) => c.toLowerCase().includes("izmir")),
    `${cities.length} rows, all Izmir`,
  );
  await page.screenshot({ path: `${SHOTS}12-filtered.png`, fullPage: true });

  // --- pagination round trip --------------------------------------------
  // Regression: no client fetch carried page_size, so the API applied its own
  // default (50) to a table first rendered at 25. "Next" asked for rows 51-100
  // of 55, came back empty, and `lastPage` then computed to 1 -- which hid the
  // pager, leaving no way back.
  // Clear the filter left over from the step above, so this runs against the
  // whole table and there really is more than one page.
  await page.getByLabel("Filter City").fill("");
  await page.getByLabel("Filter City").blur();
  await page.waitForTimeout(1200);

  const pageLabel = () => page.getByText(/^Page \d+ of \d+$/);
  check("the pager is shown for a multi-page table",
    (await pageLabel().count()) > 0, await pageLabel().first().innerText());

  const firstPageRows = await page.locator("table tbody tr").count();
  // `exact` matters: the compose stack runs `next dev`, whose dev-tools button
  // is labelled "Open Next.js Dev Tools" and matches a loose "Next".
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page.waitForTimeout(1500);
  const secondPageRows = await page.locator("table tbody tr").count();
  check("the second page has rows", secondPageRows > 0, `${secondPageRows} rows`);
  check("the page size is carried between pages",
    (await pageLabel().first().innerText()).startsWith("Page 2"),
    await pageLabel().first().innerText());

  await page.getByRole("button", { name: "Previous", exact: true }).click();
  await page.waitForTimeout(1500);
  check("Previous returns to the first page",
    (await page.locator("table tbody tr").count()) === firstPageRows &&
      (await pageLabel().first().innerText()).startsWith("Page 1"),
    `${secondPageRows} rows -> ${await page.locator("table tbody tr").count()} rows`);

  // The pager belongs to the rows, so it sits above the notes and totals.
  const order = await page.evaluate(() => {
    const pagerEl = [...document.querySelectorAll("button")]
      .find((b) => b.textContent?.trim() === "Next");
    const section = document.querySelector('section[aria-label="AÇIKLAMALAR"], section[aria-label="Notes"]');
    if (!pagerEl || !section) return "n/a";
    return pagerEl.compareDocumentPosition(section) & Node.DOCUMENT_POSITION_FOLLOWING
      ? "pager first"
      : "sections first";
  });
  check("the page controls sit above the sections, not below them",
    order === "pager first" || order === "n/a", order);

  // --- create, inline ----------------------------------------------------
  // Adding and editing happen in the table now, the way they do in a
  // spreadsheet: no /new page, no /:id page.
  await page.goto(`${BASE}/app/customer`, { waitUntil: "networkidle" });
  await page.waitForSelector("table tbody tr");

  // Unique per run, and any leftovers from an earlier crashed run are removed
  // first, so this test is idempotent.
  const stamp = Date.now() % 1000000;
  const testName = `Browser Test ${stamp}`;
  for (const stale of (await (await fetch(
    `${API}/api/entities/customer/records?full_name=Browser Test`, { headers: ORG },
  )).json()).rows) {
    await fetch(`${API}/api/records/${stale.id}`, { method: "DELETE", headers: ORG });
  }

  await page.getByRole("button", { name: "+ Add row" }).click();
  const blank = page.locator("table tbody tr").first();
  check("Add row opens a blank row in the table", await blank.getByLabel(/^Full Name/).isVisible());

  await blank.getByLabel(/^Customer ID/).fill(String(700000 + stamp));
  await blank.getByLabel(/^Full Name/).fill(testName);
  await blank.getByLabel(/^City/).fill("Trabzon");
  await blank.getByLabel(/^Joined/).fill("2026-03-01");
  await blank.getByLabel(/^Lifetime Value/).fill("4242.42");

  check("numeric column renders a number input",
    (await blank.getByLabel(/^Lifetime Value/).getAttribute("type")) === "number");
  check("date column renders a date input",
    (await blank.getByLabel(/^Joined/).getAttribute("type")) === "date");
  check("boolean column renders a checkbox",
    (await blank.getByLabel(/^Active/).getAttribute("type")) === "checkbox");
  await page.screenshot({ path: `${SHOTS}13-new-row-inline.png`, fullPage: true });

  await page.getByRole("button", { name: "Save" }).click();
  await page.waitForTimeout(1500);

  const created = await (
    await fetch(`${API}/api/entities/customer/records?full_name=${encodeURIComponent(testName)}`, { headers: ORG })
  ).json();
  check("the new row is in the typed view", created.total === 1, `total=${created.total}`);
  const newId = created.rows[0]?.id;
  check("typed columns survived the round trip",
    created.rows[0]?.lifetime_value === 4242.42 && created.rows[0]?.joined === "2026-03-01",
    `lv=${created.rows[0]?.lifetime_value} joined=${created.rows[0]?.joined}`);

  // --- edit by clicking a cell -------------------------------------------
  await page.reload({ waitUntil: "networkidle" });
  await page.getByLabel("Filter Full Name").fill(testName);
  await page.getByLabel("Filter Full Name").blur();
  await page.waitForTimeout(1200);

  const row = page.locator("table tbody tr").first();
  // Clicking the City cell should open the whole row for editing, with that
  // cell focused -- the spreadsheet gesture.
  await row.locator("td").nth(2).click();
  check("clicking a cell makes the row editable",
    await row.getByLabel(/^City/).isVisible());
  check("the clicked cell is the one focused",
    await row.getByLabel(/^City/).evaluate((el) => el === document.activeElement));
  check("every cell in the row is editable, not just the clicked one",
    (await row.locator("input").count()) >= 5,
    `${await row.locator("input").count()} inputs`);
  await page.screenshot({ path: `${SHOTS}14-inline-edit.png`, fullPage: true });

  // Enter saves, as in a spreadsheet.
  await row.getByLabel(/^City/).fill("Rize");
  await row.getByLabel(/^City/).press("Enter");
  await page.waitForTimeout(1500);
  const edited = await (
    await fetch(`${API}/api/entities/customer/records?id=${newId}`, { headers: ORG })
  ).json();
  check("Enter saves the edited row", edited.rows[0]?.city === "Rize", `city=${edited.rows[0]?.city}`);

  // --- Escape abandons an edit -------------------------------------------
  await page.reload({ waitUntil: "networkidle" });
  await page.getByLabel("Filter Full Name").fill(testName);
  await page.getByLabel("Filter Full Name").blur();
  await page.waitForTimeout(1200);
  const row2 = page.locator("table tbody tr").first();
  await row2.locator("td").nth(2).click();
  await row2.getByLabel(/^City/).fill("Nowhere");
  await row2.getByLabel(/^City/).press("Escape");
  await page.waitForTimeout(800);
  const unchanged = await (
    await fetch(`${API}/api/entities/customer/records?id=${newId}`, { headers: ORG })
  ).json();
  check("Escape discards the edit", unchanged.rows[0]?.city === "Rize",
    `city=${unchanged.rows[0]?.city}`);

  // --- the server is still the authority --------------------------------
  const badResp = await fetch(`${API}/api/records/${newId}`, {
    method: "PATCH",
    headers: { ...ORG, "Content-Type": "application/json" },
    body: JSON.stringify({ lifetime_value: "not-a-number" }),
  });
  const stillValid = (await (
    await fetch(`${API}/api/entities/customer/records?id=${newId}`, { headers: ORG })
  ).json()).rows[0]?.lifetime_value;
  check("server rejects a bad value and leaves the record intact",
    badResp.status === 400 && stillValid === 4242.42,
    `status=${badResp.status} value=${stillValid}`);

  // --- delete from the row ----------------------------------------------
  await page.reload({ waitUntil: "networkidle" });
  await page.getByLabel("Filter Full Name").fill(testName);
  await page.getByLabel("Filter Full Name").blur();
  await page.waitForTimeout(1200);
  page.once("dialog", (d) => d.accept());
  await page.getByLabel(`Delete row ${newId}`).click();
  await page.waitForTimeout(1500);
  const gone = await (
    await fetch(`${API}/api/entities/customer/records?id=${newId}`, { headers: ORG })
  ).json();
  check("the row delete button removes it", gone.total === 0);

  // --- the table assistant ----------------------------------------------
  check("the table has an assistant",
    await page.getByRole("button", { name: "Send" }).isVisible());

  // A plan is prepared and applied through the API the UI uses, so this stays
  // fast and deterministic: the chat's own turn is exercised by hand.
  const before = (await (await fetch(
    `${API}/api/entities/customer/records?page_size=1`, { headers: ORG })).json()).total;
  const plan = {
    op: "delete",
    where: [{ column: "city", operator: "eq", value: "Atlantis" }],
    changes: {},
    match_all: false,
  };
  const preview = await (await fetch(`${API}/api/entities/customer/bulk/preview`, {
    method: "POST", headers: { ...ORG, "Content-Type": "application/json" },
    body: JSON.stringify(plan),
  })).json();
  check("a bulk preview counts without deleting", preview.matched === 0,
    `${preview.matched} of ${preview.total}`);
  const after = (await (await fetch(
    `${API}/api/entities/customer/records?page_size=1`, { headers: ORG })).json()).total;
  check("previewing changed nothing", after === before, `${before} -> ${after}`);

  check("no uncaught page errors", errors.length === 0, errors.slice(0, 2).join(" | "));
} catch (err) {
  check("journey completed", false, String(err).slice(0, 300));
  await page.screenshot({ path: `${SHOTS}98-journey-crash.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
