import Link from "next/link";
import { getHealth, listWorkbooks, PUBLIC_API_BASE, ORG_ID, type Health, type Workbook } from "@/lib/api";
import { WorkbookList } from "@/components/WorkbookList";

export const dynamic = "force-dynamic";

async function load(): Promise<{
  health: Health | null;
  workbooks: Workbook[];
  error: string | null;
}> {
  try {
    const [health, workbooks] = await Promise.all([getHealth(), listWorkbooks()]);
    return { health, workbooks, error: null };
  } catch (e) {
    return {
      health: null,
      workbooks: [],
      error: e instanceof Error ? e.message : String(e),
    };
  }
}

export default async function Home() {
  const { health, workbooks, error } = await load();
  const ok = health?.status === "ok";

  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <div className="flex items-baseline gap-4">
        <h1 className="text-3xl font-semibold tracking-tight">Gridless</h1>
        <span
          className={`inline-block h-2 w-2 rounded-full ${ok ? "bg-emerald-500" : "bg-red-500"}`}
          title={ok ? "Backend healthy" : "Backend unreachable"}
        />
        <span className="ml-auto font-mono text-xs text-neutral-500">
          {PUBLIC_API_BASE} · org {ORG_ID}
        </span>
      </div>
      <p className="mt-2 text-neutral-500">
        Turn a folder of spreadsheets into a working internal app.
      </p>

      {error && (
        <p className="mt-6 rounded border border-red-200 bg-red-50 p-3 font-mono text-xs text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          Backend unreachable: {error}
        </p>
      )}

      <section className="mt-10">
        <div className="flex items-center">
          <h2 className="font-medium">Workbooks</h2>
          <div className="ml-auto flex items-center gap-2">
            <Link
              href="/app"
              className="rounded border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
            >
              Your app
            </Link>
            <Link
              href="/ask"
              className="rounded border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
            >
              Ask
            </Link>
            <Link
              href="/upload"
              className="rounded bg-neutral-900 px-3 py-1.5 text-sm text-white hover:bg-neutral-700 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200"
            >
              Upload
            </Link>
          </div>
        </div>
        <div className="mt-4">
          <WorkbookList initial={workbooks} />
        </div>
      </section>

      <p className="mt-10 text-xs text-neutral-400">
        Nothing here is mocked — every screen calls the real API.
      </p>
    </main>
  );
}
