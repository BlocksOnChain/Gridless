const STYLES: Record<string, string> = {
  uploaded: "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300",
  analysing: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  proposed: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-300",
  committed: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  failed: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
        STYLES[status] ?? STYLES.uploaded
      }`}
    >
      {status}
    </span>
  );
}
