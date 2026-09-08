/**
 * Review -> commit -> the generated app grows.
 *
 * The old version of this test manufactured a blocker (an undecided
 * relationship) and resolved it by clicking Reject. Neither half exists any
 * more: relationships are settled by the confidence threshold in the backend's
 * `inference.policy`, not by the reviewer, so what is worth asserting now is
 * that an undecided relationship *cannot* block a commit nobody is able to
 * unblock -- and that nothing under the threshold reaches the graph.
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE ?? "http://localhost:3000";
const API = process.env.API ?? "http://127.0.0.1:8010";
const ORG = { "X-Org-Id": "1" };
const THRESHOLD = Number(process.env.THRESHOLD ?? 0.85);
const SHOTS = new URL("./shots/", import.meta.url).pathname;
mkdirSync(SHOTS, { recursive: true });

const results = [];
const check = (n, ok, d = "") => { results.push({ n, ok }); console.log(`${ok ? "PASS" : "FAIL"}  ${n}${d ? `  — ${d}` : ""}`); };

const api = async (p, o = {}) =>
  (await fetch(`${API}${p}`, { ...o, headers: { ...ORG, "Content-Type": "application/json", ...(o.headers ?? {}) } })).json();

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });

try {
  // Any proposed workbook with entities will do.
  const workbooks = await api("/api/workbooks");
  let target = null;
  for (const wb of workbooks) {
    if (wb.status !== "proposed" || wb.entity_count === 0) continue;
    target = wb;
    break;
  }
  check("found a proposed workbook to commit", !!target, target ? `id=${target.id}` : "none");

  if (target) {
    const proposal = await api(`/api/workbooks/${target.id}/proposal`);

    const pre = await api(`/api/workbooks/${target.id}/preflight`);
    check("a reviewed proposal is committable", pre.can_commit, pre.problems.join("; "));

    const undecided = (proposal.relationships ?? []).filter((r) => r.needs_review && !r.rejected);
    check(
      "an undecided relationship does not block the commit",
      pre.can_commit,
      `${undecided.length} undecided`,
    );

    await page.goto(`${BASE}/workbooks/${target.id}/review`, { waitUntil: "networkidle" });
    check(
      "the review screen asks for no relationship decisions",
      (await page.getByRole("button", { name: "Accept" }).count()) === 0,
    );

    await page.getByRole("button", { name: /Looks right/ }).click();
    await page.waitForURL("**/app**", { timeout: 60000 });
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${SHOTS}21-after-commit.png`, fullPage: true });

    const after = (await api("/api/workbooks")).find((w) => w.id === target.id);
    check("workbook is committed", after.status === "committed", `status=${after.status}`);

    // The threshold is the verdict: after a commit, nothing that is still
    // accepted may sit below it, and nothing is left waiting for a human.
    const settled = await api(`/api/workbooks/${target.id}/proposal`);
    const accepted = (settled.relationships ?? []).filter((r) => !r.rejected);
    const weak = accepted.filter((r) => !r.user_confirmed && r.confidence < THRESHOLD);
    check(
      "no relationship below the threshold survives the commit",
      weak.length === 0,
      weak.map((r) => `${r.name}@${r.confidence}`).join(", "),
    );
    check(
      "nothing is left awaiting a human decision",
      (settled.relationships ?? []).every((r) => !r.needs_review),
    );

    const entities = await api("/api/entities");
    check("its entities appear in the generated app",
      entities.some((e) => e.record_count > 0),
      `${entities.length} entities total`);

    // Every committed entity must be queryable through its view.
    let queryable = 0;
    for (const e of entities) {
      const r = await api(`/api/entities/${e.table_slug}/records?page_size=1`);
      if (typeof r.total === "number") queryable += 1;
    }
    check("every committed entity is queryable", queryable === entities.length,
      `${queryable}/${entities.length}`);
  }
} catch (err) {
  check("commit flow completed", false, String(err).slice(0, 300));
  await page.screenshot({ path: `${SHOTS}97-commit-crash.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
