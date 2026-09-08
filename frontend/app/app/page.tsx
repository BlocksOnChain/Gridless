import Link from "next/link";
import { attempt, listEntities } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function AppHome() {
  const result = await attempt(listEntities);

  if (!result.ok) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-16">
        <Link href="/" className="text-sm text-neutral-500 underline underline-offset-2">
          ← Workbooks
        </Link>
        <h1 className="mt-4 text-2xl font-semibold">Your app</h1>
        <p className="mt-3 rounded border border-red-200 bg-red-50 p-3 font-mono text-xs text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {result.error}
        </p>
      </main>
    );
  }

  const entities = result.value;
  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <Link href="/" className="text-sm text-neutral-500 underline underline-offset-2">
        ← Workbooks
      </Link>
      <h1 className="mt-4 text-2xl font-semibold tracking-tight">Your app</h1>
      <p className="mt-2 text-sm text-neutral-500">
        These screens are generated from the committed schema. Nothing here was hand-written
        per entity.
      </p>

      {entities.length === 0 ? (
        <p className="mt-8 text-sm text-neutral-500">
          Nothing committed yet. Review a workbook and press Commit.
        </p>
      ) : (
        <ul className="mt-8 divide-y divide-neutral-200 dark:divide-neutral-800">
          {entities.map((e) => (
            <li key={e.id} className="flex items-center gap-4 py-3">
              <Link
                href={`/app/${e.table_slug}`}
                className="font-medium underline underline-offset-2"
              >
                {e.name}
              </Link>
              <code className="font-mono text-xs text-neutral-500">{e.table_slug}</code>
              <span className="ml-auto text-sm text-neutral-500">
                {e.record_count} records · {e.columns.length} fields
              </span>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
