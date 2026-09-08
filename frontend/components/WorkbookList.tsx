"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { analyseWorkbook, listWorkbooks, type AnalyseResult, type Workbook } from "@/lib/api";
import { StatusBadge } from "./StatusBadge";

/**
 * The initial list is fetched on the server and passed in; this component only
 * re-fetches after it has caused a change. That keeps first paint server-rendered
 * and avoids a fetch-on-mount effect.
 */
export function WorkbookList({ initial }: { initial: Workbook[] }) {
  const [workbooks, setWorkbooks] = useState<Workbook[]>(initial);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [results, setResults] = useState<Record<number, AnalyseResult>>({});

  const refresh = useCallback(async () => {
    try {
      setWorkbooks(await listWorkbooks());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  async function analyse(id: number) {
    setBusy(id);
    try {
      const result = await analyseWorkbook(id);
      setResults((prev) => ({ ...prev, [id]: result }));
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (workbooks.length === 0) {
    return (
      <p className="text-sm text-neutral-500">
        No workbooks yet.{" "}
        <Link href="/upload" className="underline underline-offset-2">
          Upload one
        </Link>
        .
      </p>
    );
  }

  return (
    <>
      {error && (
        <p className="mb-4 rounded border border-red-200 bg-red-50 p-3 font-mono text-xs text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {error}
        </p>
      )}
      <ul className="divide-y divide-neutral-200 dark:divide-neutral-800">
        {workbooks.map((wb) => {
          const result = results[wb.id];
          return (
            <li key={wb.id} className="py-3">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-medium">{wb.original_filename}</span>
                <StatusBadge status={wb.status} />
                <span className="text-sm text-neutral-500">
                  {wb.entity_count} {wb.entity_count === 1 ? "entity" : "entities"}
                </span>
                {wb.entity_count > 0 && (
                  <Link
                    href={`/workbooks/${wb.id}/review`}
                    className="rounded border border-neutral-300 px-3 py-1 text-sm hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
                  >
                    Review
                  </Link>
                )}
                <button
                  onClick={() => analyse(wb.id)}
                  disabled={busy === wb.id}
                  className="ml-auto rounded border border-neutral-300 px-3 py-1 text-sm hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
                >
                  {busy === wb.id
                    ? "Analysing…"
                    : wb.status === "uploaded"
                      ? "Analyse"
                      : "Re-analyse"}
                </button>
              </div>

              {wb.error && (
                <p className="mt-2 font-mono text-xs text-red-600 dark:text-red-400">{wb.error}</p>
              )}

              {result && (
                <p className="mt-2 font-mono text-xs text-neutral-500">
                  {result.sheets} tables · {result.entities} entities · {result.fields} fields ·{" "}
                  {result.relationships} relationships
                  {result.low_confidence.length > 0 && (
                    <span className="text-amber-600 dark:text-amber-400">
                      {" "}
                      · low confidence: {result.low_confidence.join(", ")}
                    </span>
                  )}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </>
  );
}
