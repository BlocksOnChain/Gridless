/**
 * The parts of a sheet that were never rows.
 *
 * A production report ends with an AÇIKLAMALAR block: "*HAT 5 SABAH
 * VARDİYASINDA BIÇAK ARIZASINDAN DOLAYI...". It is the most human information
 * on the sheet and a table cannot hold it -- forced into columns it became a
 * "Machine Capacity" column whose every value was that same paragraph.
 *
 * So it is rendered as what it is: a section under the table, in the words the
 * author wrote, with their line breaks intact.
 */
import type { SectionMeta } from "@/lib/api";

const TONE: Record<string, string> = {
  note: "border-amber-200 bg-amber-50/60 dark:border-amber-900/60 dark:bg-amber-950/20",
  summary: "border-neutral-200 bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900/60",
  meta: "border-neutral-200 bg-transparent dark:border-neutral-800",
};

export function Sections({ sections }: { sections: SectionMeta[] }) {
  if (!sections.length) return null;

  return (
    <div className="mt-6 space-y-4">
      {sections.map((section) => (
        <section
          key={section.id}
          aria-label={section.title || section.kind}
          className={`rounded-lg border p-4 ${TONE[section.kind] ?? TONE.meta}`}
        >
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
            {section.title || (section.kind === "summary" ? "Totals" : "Notes")}
          </h3>
          {/* whitespace-pre-line: the author's line breaks are the structure. */}
          <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-neutral-700 dark:text-neutral-300">
            {section.body}
          </p>
        </section>
      ))}
    </div>
  );
}
