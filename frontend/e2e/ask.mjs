/** The ask screen: real question, real SQL, always auditable. */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE ?? "http://localhost:3000";
const SHOTS = new URL("./shots/", import.meta.url).pathname;
mkdirSync(SHOTS, { recursive: true });

const results = [];
const check = (n, ok, d = "") => { results.push({ n, ok }); console.log(`${ok ? "PASS" : "FAIL"}  ${n}${d ? `  — ${d}` : ""}`); };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });

try {
  await page.goto(`${BASE}/ask`, { waitUntil: "networkidle" });
  check("ask page renders", await page.getByRole("heading", { name: "Ask" }).isVisible());

  await page.getByRole("button", { name: /highest lifetime value/ }).click();
  await page.waitForSelector("pre", { timeout: 180000 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${SHOTS}30-ask.png`, fullPage: true });

  const sql = await page.locator("pre").first().innerText();
  check("SQL is shown by default", /select/i.test(sql), sql.split("\n")[0].slice(0, 60));
  check("SQL is a SELECT only", !/\b(insert|update|delete|drop)\b/i.test(sql));

  const rows = await page.locator("table tbody tr").count();
  check("result rows are rendered", rows > 0, `${rows} rows`);

  const answer = await page.locator("main section p").first().innerText();
  check("answer is a sentence, not a dump", answer.length > 30 && answer.length < 1200,
    `${answer.length} chars`);

  // Every number in the answer should also appear in the table the user can see.
  const table = await page.locator("table").first().innerText();
  const numbers = (answer.match(/\d[\d,]*\.\d{2}/g) ?? []).map((n) => n.replace(/,/g, ""));
  const auditable = numbers.every((n) => table.replace(/,/g, "").includes(n));
  check("numbers in the answer appear in the shown rows", auditable,
    numbers.length ? numbers.join(", ") : "no decimals to check");

  await page.getByRole("button", { name: "Hide SQL" }).click();
  await page.waitForTimeout(200);
  check("SQL can be collapsed", (await page.locator("pre").count()) === 0);
} catch (err) {
  check("ask flow completed", false, String(err).slice(0, 250));
  await page.screenshot({ path: `${SHOTS}96-ask-crash.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
