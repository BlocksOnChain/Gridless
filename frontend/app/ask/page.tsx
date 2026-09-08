"use client";

import { useState } from "react";
import Link from "next/link";
import { ask, type AskResponse } from "@/lib/api";

const EXAMPLES = [
  "Which 3 customers have the highest lifetime value?",
  "What is the total premium per product?",
  "How many policies are lapsed?",
];

export default function AskPage() {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showSql, setShowSql] = useState(true);

  async function submit(q: string) {
    if (!q.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await ask(q));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-4xl px-6 py-12">
      <Link href="/" className="text-sm text-neutral-500 underline underline-offset-2">
        ← Workbooks
      </Link>
      <h1 className="mt-3 text-2xl font-semibold tracking-tight">Ask</h1>
      <p className="mt-2 text-sm text-neutral-500">
        Questions are answered by querying the committed data. The SQL that produced the
        answer is always shown — never trust a number you cannot audit.
      </p>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit(question);
        }}
        className="mt-6 flex gap-2"
      >
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. which customers have a policy expiring soon?"
          aria-label="Question"
          className="flex-1 rounded border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-950"
        />
        <button
          type="submit"
          disabled={busy || !question.trim()}
          className="rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900"
        >
          {busy ? "Thinking…" : "Ask"}
        </button>
      </form>

      <div className="mt-3 flex flex-wrap gap-2">
        {EXAMPLES.map((e) => (
          <button
            key={e}
            onClick={() => {
              setQuestion(e);
              void submit(e);
            }}
            disabled={busy}
            className="rounded-full border border-neutral-300 px-3 py-1 text-xs text-neutral-600 hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-900"
          >
            {e}
          </button>
        ))}
      </div>

      {busy && (
        <p className="mt-8 text-sm text-neutral-500">
          Writing SQL, running it, and checking the result…
        </p>
      )}

      {error && (
        <p
          role="alert"
          className="mt-8 rounded border border-red-200 bg-red-50 p-3 font-mono text-xs text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300"
        >
          {error}
        </p>
      )}

      {result && (
        <section className="mt-8 space-y-5">
          <p className="text-[15px] leading-relaxed">{result.answer}</p>

          <div>
            <button
              onClick={() => setShowSql((v) => !v)}
              className="text-xs text-neutral-500 underline underline-offset-2"
            >
              {showSql ? "Hide SQL" : "Show SQL"}
            </button>
            {showSql && (
              <pre className="mt-2 overflow-x-auto rounded border border-neutral-200 bg-neutral-50 p-3 font-mono text-[11px] leading-relaxed dark:border-neutral-800 dark:bg-neutral-900">
                {result.sql}
              </pre>
            )}
            <p className="mt-1 text-[11px] text-neutral-400">
              {result.row_count} row{result.row_count === 1 ? "" : "s"}
              {result.truncated && " (truncated)"} · {result.attempts} quer
              {result.attempts === 1 ? "y" : "ies"} run
            </p>
          </div>

          {result.rows.length > 0 && (
            <div className="overflow-x-auto rounded border border-neutral-200 dark:border-neutral-800">
              <table className="w-full text-sm">
                <thead className="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500 dark:bg-neutral-900">
                  <tr>
                    {result.columns.map((c) => (
                      <th key={c} className="px-3 py-2 font-medium">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
                  {result.rows.slice(0, 50).map((row, i) => (
                    <tr key={i}>
                      {result.columns.map((c) => (
                        <td key={c} className="px-3 py-2">
                          {row[c] === null || row[c] === undefined ? "—" : String(row[c])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </main>
  );
}
