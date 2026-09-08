"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  DATA_TYPES,
  addField,
  addSection,
  commitWorkbook,
  deleteField,
  deleteSection,
  getPreflight,
  patchEntity,
  patchField,
  patchSection,
  type DataType,
  type Entity,
  type Field,
  type Proposal,
  type Section,
} from "@/lib/api";
import { ReviewChat } from "./ReviewChat";

/**
 * Check the columns before the spreadsheet becomes an app.
 *
 * Deliberately not shown here: confidence scores, relationships, detected
 * header rows, table and column slugs. Those are how the inference works, not
 * decisions the person in front of this screen can make -- they came here with
 * a spreadsheet, and a page of measurements told them they had homework in a
 * subject they never studied. Relationships are settled by the confidence
 * threshold in the backend's `inference.policy`; everything the pipeline is
 * unsure about it drops rather than delegating upward.
 *
 * What is left is the part a person genuinely knows better than any model: what
 * each column is called, what kind of value it holds, which one identifies a
 * row, and which columns should not be imported at all. All of it editable by
 * hand, and all of it editable by asking.
 */

/** Type names in the user's vocabulary, not the database's. */
const TYPE_LABELS: Record<DataType, string> = {
  text: "Text",
  integer: "Whole number",
  numeric: "Number",
  date: "Date",
  datetime: "Date & time",
  boolean: "Yes / no",
  json: "Structured",
};

function sample(field: Field): string {
  const values = (field.sample_values ?? []).filter((v) => v !== null && v !== "");
  if (!values.length) return field.source_header ? "—" : "empty until you fill it in";
  return values.slice(0, 3).map(String).join(", ");
}

export function ReviewScreen({ initial }: { initial: Proposal }) {
  const [entities, setEntities] = useState<Entity[]>(initial.entities);
  const [selectedId, setSelectedId] = useState<number | null>(initial.entities[0]?.id ?? null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(0);
  const [committing, setCommitting] = useState(false);
  const [blockers, setBlockers] = useState<string[] | null>(null);
  const [newColumn, setNewColumn] = useState("");
  const [newType, setNewType] = useState<DataType>("text");
  const [newSection, setNewSection] = useState("");
  const router = useRouter();

  const selected = entities.find((e) => e.id === selectedId) ?? null;

  const totals = useMemo(
    () => ({
      tables: entities.length,
      columns: entities.reduce((n, e) => n + e.fields.length, 0),
    }),
    [entities],
  );

  async function run<T>(fn: () => Promise<T>, apply: (result: T) => void) {
    setSaving((n) => n + 1);
    setError(null);
    try {
      apply(await fn());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving((n) => n - 1);
    }
  }

  const replaceField = (updated: Field) =>
    setEntities((prev) =>
      prev.map((e) =>
        e.id !== selectedId
          ? e
          : {
              ...e,
              fields: e.fields.map((f) =>
                f.id === updated.id
                  ? updated
                  : // The server clears any other ID column; mirror that locally
                    // so the UI cannot show two.
                    updated.is_primary_key
                    ? { ...f, is_primary_key: false }
                    : f,
              ),
            },
      ),
    );

  const replaceEntity = (updated: Entity) =>
    setEntities((prev) => prev.map((e) => (e.id === updated.id ? { ...e, ...updated } : e)));

  const putSections = (entityId: number, sections: Section[]) =>
    setEntities((prev) =>
      prev.map((e) => (e.id === entityId ? { ...e, sections } : e)),
    );

  return (
    <div className="mx-auto max-w-[1400px] px-6 py-8">
      <header className="mb-6">
        <Link href="/" className="text-sm text-neutral-500 underline underline-offset-2">
          ← Workbooks
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">
            {initial.workbook.original_filename}
          </h1>
          <span className="text-sm text-neutral-500">
            {totals.tables} {totals.tables === 1 ? "table" : "tables"} · {totals.columns}{" "}
            {totals.columns === 1 ? "column" : "columns"}
          </span>
          <div className="ml-auto flex items-center gap-3">
            {saving > 0 && <span className="text-xs text-neutral-500">saving…</span>}
            <button
              onClick={async () => {
                setCommitting(true);
                setError(null);
                setBlockers(null);
                try {
                  // Ask what would block first, so problems arrive as a list
                  // rather than one failed commit at a time.
                  const pre = await getPreflight(initial.workbook.id);
                  if (!pre.can_commit) {
                    setBlockers(pre.problems);
                    return;
                  }
                  const result = await commitWorkbook(initial.workbook.id);
                  router.push(`/app?committed=${result.workbook_id}`);
                } catch (e) {
                  setError(e instanceof Error ? e.message : String(e));
                } finally {
                  setCommitting(false);
                }
              }}
              disabled={committing}
              className="rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white hover:bg-neutral-700 disabled:opacity-50 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200"
            >
              {committing ? "Creating your app…" : "Looks right — build it"}
            </button>
          </div>
        </div>
        <p className="mt-2 text-sm text-neutral-500">
          These are the columns we read from your file. Change anything that looks wrong, then
          build the app.
        </p>
        {blockers && (
          <div
            role="alert"
            className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
          >
            <p className="font-medium">Fix these first:</p>
            <ul className="mt-1 list-disc pl-5">
              {blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          </div>
        )}
        {error && (
          <p
            role="alert"
            className="mt-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300"
          >
            {error}
          </p>
        )}
      </header>

      <div className="grid gap-6 lg:grid-cols-[200px_minmax(0,1fr)_360px]">
        {/* ---------------- Left: tables ---------------- */}
        <aside>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Tables
          </h2>
          <ul className="space-y-1">
            {entities.map((e) => (
              <li key={e.id}>
                <button
                  onClick={() => setSelectedId(e.id)}
                  aria-current={e.id === selectedId}
                  className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm ${
                    e.id === selectedId
                      ? "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900"
                      : "hover:bg-neutral-100 dark:hover:bg-neutral-900"
                  }`}
                >
                  <span className="truncate">{e.name}</span>
                  <span
                    className={`ml-auto text-xs ${
                      e.id === selectedId ? "opacity-70" : "text-neutral-400"
                    }`}
                  >
                    {e.fields.length}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        {/* ---------------- Centre: columns ---------------- */}
        <section className="min-w-0">
          {selected ? (
            <>
              <div className="mb-3">
                <label
                  htmlFor="table-name"
                  className="block text-xs font-semibold uppercase tracking-wide text-neutral-500"
                >
                  Table name
                </label>
                <input
                  id="table-name"
                  value={selected.name}
                  aria-label="Table name"
                  onChange={(ev) => replaceEntity({ ...selected, name: ev.target.value })}
                  onBlur={(ev) =>
                    run(() => patchEntity(selected.id, { name: ev.target.value }), replaceEntity)
                  }
                  className="mt-1 rounded border border-neutral-300 px-2 py-1 text-lg font-medium dark:border-neutral-700 dark:bg-neutral-950"
                />
              </div>

              <div className="overflow-x-auto rounded-lg border border-neutral-200 dark:border-neutral-800">
                <table className="w-full text-sm">
                  <thead className="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500 dark:bg-neutral-900">
                    <tr>
                      <th className="px-3 py-2 font-medium">Column</th>
                      <th className="px-3 py-2 font-medium">Kind of value</th>
                      <th className="px-3 py-2 font-medium" title="Identifies each row">
                        ID
                      </th>
                      <th className="px-3 py-2 font-medium">From your file</th>
                      <th className="px-3 py-2 font-medium">
                        <span className="sr-only">Remove</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
                    {selected.fields.map((f) => (
                      <tr key={f.id}>
                        <td className="px-3 py-2">
                          <input
                            value={f.name}
                            aria-label={`Name for ${f.source_header || f.name}`}
                            onChange={(ev) => replaceField({ ...f, name: ev.target.value })}
                            onBlur={(ev) =>
                              run(() => patchField(f.id, { name: ev.target.value }), replaceField)
                            }
                            className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 hover:border-neutral-300 focus:border-neutral-400 dark:hover:border-neutral-700"
                          />
                          {/* Only when it would tell the user something: the
                              original header is how they find this column in
                              their own file. */}
                          {f.source_header && f.source_header !== f.name && (
                            <div className="px-1 text-[11px] text-neutral-400">
                              was “{f.source_header}”
                            </div>
                          )}
                        </td>
                        <td className="px-3 py-2">
                          <select
                            value={f.data_type}
                            aria-label={`Kind of value for ${f.name}`}
                            onChange={(ev) =>
                              run(
                                () =>
                                  patchField(f.id, { data_type: ev.target.value as DataType }),
                                replaceField,
                              )
                            }
                            className="rounded border border-neutral-300 bg-transparent px-1.5 py-1 text-xs dark:border-neutral-700"
                          >
                            {DATA_TYPES.map((t) => (
                              <option key={t} value={t}>
                                {TYPE_LABELS[t]}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td className="px-3 py-2">
                          <input
                            type="radio"
                            name={`pk-${selected.id}`}
                            checked={f.is_primary_key}
                            aria-label={`${f.name} identifies each row`}
                            onChange={() =>
                              run(
                                () => patchField(f.id, { is_primary_key: true }),
                                replaceField,
                              )
                            }
                          />
                        </td>
                        <td className="max-w-[20rem] truncate px-3 py-2 text-[11px] text-neutral-500">
                          {sample(f)}
                        </td>
                        <td className="px-3 py-2 text-right">
                          <button
                            aria-label={`Remove ${f.name}`}
                            title="Remove this column"
                            onClick={() => {
                              if (
                                !window.confirm(
                                  `Remove “${f.name}”? Its data will not be imported.`,
                                )
                              )
                                return;
                              run(
                                async () => {
                                  await deleteField(f.id);
                                  return f.id;
                                },
                                (removedId) =>
                                  setEntities((prev) =>
                                    prev.map((e) =>
                                      e.id !== selected.id
                                        ? e
                                        : {
                                            ...e,
                                            fields: e.fields.filter((x) => x.id !== removedId),
                                          },
                                    ),
                                  ),
                              );
                            }}
                            className="rounded px-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-red-600 dark:hover:bg-neutral-900"
                          >
                            ✕
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <form
                onSubmit={(ev) => {
                  ev.preventDefault();
                  const name = newColumn.trim();
                  if (!name) return;
                  run(
                    () => addField(selected.id, { name, data_type: newType }),
                    (created) => {
                      setEntities((prev) =>
                        prev.map((e) =>
                          e.id !== selected.id ? e : { ...e, fields: [...e.fields, created] },
                        ),
                      );
                      setNewColumn("");
                      setNewType("text");
                    },
                  );
                }}
                className="mt-3 flex flex-wrap items-center gap-2"
              >
                <input
                  value={newColumn}
                  onChange={(ev) => setNewColumn(ev.target.value)}
                  placeholder="New column name"
                  aria-label="New column name"
                  className="rounded border border-neutral-300 px-2 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-950"
                />
                <select
                  value={newType}
                  onChange={(ev) => setNewType(ev.target.value as DataType)}
                  aria-label="Kind of value for the new column"
                  className="rounded border border-neutral-300 bg-transparent px-2 py-1.5 text-sm dark:border-neutral-700"
                >
                  {DATA_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {TYPE_LABELS[t]}
                    </option>
                  ))}
                </select>
                <button
                  type="submit"
                  disabled={!newColumn.trim()}
                  className="rounded border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-900"
                >
                  Add column
                </button>
                <span className="text-xs text-neutral-500">
                  Added columns start empty — you fill them in from the app.
                </span>
              </form>

              {/*
                Sections: what the sheet said that no column could hold. They
                are shown here because they are part of the page being built,
                and because the reviewer is the only person who can tell a note
                worth keeping from a stray line of text.
              */}
              <div className="mt-8">
                <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
                  Notes and totals under this table
                </h2>
                {selected.sections.length === 0 ? (
                  <p className="mt-2 text-sm text-neutral-500">
                    Nothing yet. Anything in the sheet that was not a column shows up
                    here.
                  </p>
                ) : (
                  <ul className="mt-2 space-y-3">
                    {selected.sections.map((section) => (
                      <li
                        key={section.id}
                        className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800"
                      >
                        <div className="flex items-center gap-2">
                          <input
                            value={section.title}
                            aria-label={`Title of section ${section.id}`}
                            onChange={(ev) =>
                              putSections(
                                selected.id,
                                selected.sections.map((x) =>
                                  x.id === section.id
                                    ? { ...x, title: ev.target.value }
                                    : x,
                                ),
                              )
                            }
                            onBlur={(ev) =>
                              run(
                                () => patchSection(section.id, { title: ev.target.value }),
                                (updated) =>
                                  putSections(
                                    selected.id,
                                    selected.sections.map((x) =>
                                      x.id === updated.id ? updated : x,
                                    ),
                                  ),
                              )
                            }
                            className="w-full rounded border border-transparent px-1 py-0.5 text-sm font-medium hover:border-neutral-300 focus:border-neutral-400 dark:hover:border-neutral-700"
                          />
                          <span className="shrink-0 rounded bg-neutral-100 px-1.5 py-0.5 text-[11px] text-neutral-500 dark:bg-neutral-900">
                            {section.kind}
                          </span>
                          <button
                            aria-label={`Remove section ${section.title}`}
                            title="Remove this section"
                            onClick={() => {
                              if (!window.confirm(`Remove “${section.title}”?`)) return;
                              run(
                                async () => {
                                  await deleteSection(section.id);
                                  return section.id;
                                },
                                (removed) =>
                                  putSections(
                                    selected.id,
                                    selected.sections.filter((x) => x.id !== removed),
                                  ),
                              );
                            }}
                            className="shrink-0 rounded px-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-red-600 dark:hover:bg-neutral-900"
                          >
                            ✕
                          </button>
                        </div>
                        <textarea
                          value={section.body}
                          aria-label={`Text of section ${section.title}`}
                          rows={Math.min(8, Math.max(2, section.body.split("\n").length))}
                          onChange={(ev) =>
                            putSections(
                              selected.id,
                              selected.sections.map((x) =>
                                x.id === section.id ? { ...x, body: ev.target.value } : x,
                              ),
                            )
                          }
                          onBlur={(ev) =>
                            run(
                              () => patchSection(section.id, { body: ev.target.value }),
                              (updated) =>
                                putSections(
                                  selected.id,
                                  selected.sections.map((x) =>
                                    x.id === updated.id ? updated : x,
                                  ),
                                ),
                            )
                          }
                          className="mt-2 w-full rounded border border-neutral-200 bg-transparent px-2 py-1.5 text-sm leading-relaxed dark:border-neutral-800"
                        />
                        {section.source_range && (
                          <p className="mt-1 text-[11px] text-neutral-400">
                            from {section.source_range} in your sheet
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                )}

                <form
                  onSubmit={(ev) => {
                    ev.preventDefault();
                    const title = newSection.trim();
                    if (!title) return;
                    run(
                      () => addSection(selected.id, { title, body: "" }),
                      (created) => {
                        putSections(selected.id, [...selected.sections, created]);
                        setNewSection("");
                      },
                    );
                  }}
                  className="mt-3 flex flex-wrap items-center gap-2"
                >
                  <input
                    value={newSection}
                    onChange={(ev) => setNewSection(ev.target.value)}
                    placeholder="New section heading"
                    aria-label="New section heading"
                    className="rounded border border-neutral-300 px-2 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-950"
                  />
                  <button
                    type="submit"
                    disabled={!newSection.trim()}
                    className="rounded border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-900"
                  >
                    Add section
                  </button>
                </form>
              </div>
            </>
          ) : (
            <p className="text-sm text-neutral-500">
              We could not read any tables from this file.
            </p>
          )}
        </section>

        {/* ---------------- Right: the assistant ---------------- */}
        <ReviewChat
          workbookId={initial.workbook.id}
          onProposal={(proposal) => {
            setEntities(proposal.entities);
            // The chat can remove the selected table's last column, or the
            // table itself in a later build; fall back rather than blanking.
            if (!proposal.entities.some((e) => e.id === selectedId)) {
              setSelectedId(proposal.entities[0]?.id ?? null);
            }
          }}
        />
      </div>
    </div>
  );
}
