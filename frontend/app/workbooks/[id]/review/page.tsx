import Link from "next/link";
import { attempt, getProposal } from "@/lib/api";
import { ReviewScreen } from "@/components/ReviewScreen";

export const dynamic = "force-dynamic";

export default async function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const result = await attempt(() => getProposal(Number(id)));

  if (result.ok) return <ReviewScreen initial={result.value} />;

  return (
    <main className="mx-auto max-w-2xl px-6 py-16">
      <Link href="/" className="text-sm text-neutral-500 underline underline-offset-2">
        ← Workbooks
      </Link>
      <h1 className="mt-4 text-xl font-semibold">Could not load this proposal</h1>
      <p className="mt-3 rounded border border-red-200 bg-red-50 p-3 font-mono text-xs text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
        {result.error}
      </p>
      <p className="mt-4 text-sm text-neutral-500">
        A workbook must be analysed before it has a proposal to review.
      </p>
    </main>
  );
}
