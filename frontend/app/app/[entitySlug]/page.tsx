import Link from "next/link";
import { attempt, listRecords } from "@/lib/api";
import { RecordTable } from "@/components/RecordTable";

export const dynamic = "force-dynamic";

export default async function EntityListPage({
  params,
}: {
  params: Promise<{ entitySlug: string }>;
}) {
  const { entitySlug } = await params;
  const result = await attempt(() => listRecords(entitySlug, { pageSize: 25 }));

  if (!result.ok) {
    return (
      <main className="mx-auto max-w-2xl px-6 py-16">
        <Link href="/app" className="text-sm text-neutral-500 underline underline-offset-2">
          ← Your app
        </Link>
        <h1 className="mt-4 text-xl font-semibold">Could not load {entitySlug}</h1>
        <p className="mt-3 rounded border border-red-200 bg-red-50 p-3 font-mono text-xs text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {result.error}
        </p>
      </main>
    );
  }

  return (
    // Wider than the other pages: the table now carries its own assistant.
    <main className="mx-auto max-w-[1500px] px-6 py-10">
      <Link href="/app" className="text-sm text-neutral-500 underline underline-offset-2">
        ← Your app
      </Link>
      <h1 className="mt-3 mb-4 text-2xl font-semibold tracking-tight">{result.value.entity}</h1>
      <RecordTable slug={entitySlug} initial={result.value} />
    </main>
  );
}
