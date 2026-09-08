/** High-DPI screenshots for the marketing video. Real UI, real data. */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE ?? "http://localhost:3000";
const API = process.env.API ?? "http://127.0.0.1:8010";
const ORG = { "X-Org-Id": "1" };
const OUT = new URL("./hq/", import.meta.url).pathname;
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2, // crisp at 1080p
});

const shot = async (name, url, opts = {}) => {
  await page.goto(url, { waitUntil: "networkidle" });
  if (opts.wait) await page.waitForTimeout(opts.wait);
  if (opts.before) await opts.before();
  await page.screenshot({ path: `${OUT}${name}.png`, fullPage: false });
  console.log("shot", name);
};

// Find a workbook with a proposal and one with committed data.
const wbs = await (await fetch(`${API}/api/workbooks`, { headers: ORG })).json();
let reviewId = null;
for (const w of wbs) {
  if (w.entity_count === 0) continue;
  const p = await (await fetch(`${API}/api/workbooks/${w.id}/proposal`, { headers: ORG })).json();
  // Any multi-table proposal makes a representative shot; the review screen
  // no longer renders relationships, so their count is irrelevant to it.
  if (p.entities?.length >= 2) { reviewId = w.id; break; }
}

await shot("landing", BASE);
await shot("upload", `${BASE}/upload`);
if (reviewId) await shot("review", `${BASE}/workbooks/${reviewId}/review`, { wait: 600 });
await shot("app-home", `${BASE}/app`);
await shot("records", `${BASE}/app/customer`, { wait: 400 });
await shot("new-record", `${BASE}/app/customer/new`);

await shot("ask", `${BASE}/ask`, {
  wait: 300,
  before: async () => {
    await page.getByRole("button", { name: /highest lifetime value/ }).click();
    await page.waitForSelector("table tbody tr", { timeout: 180000 });
    await page.waitForTimeout(600);
  },
});

await browser.close();
console.log("done ->", OUT);
