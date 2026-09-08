"use client";

import { useCallback, useState } from "react";
import {
  createRecord,
  deleteRecord,
  listRecords,
  updateRecord,
  type ColumnMeta,
  type RecordPage,
  type RecordRow,
} from "@/lib/api";
import { INPUT_TYPE, display, toDraft, toPayload, type Draft } from "@/lib/cells";
import { Sections } from "./Sections";
import { TableChat } from "./TableChat";

/**
 * The table people work in, edited the way a spreadsheet is.
 *
 * Adding and editing happen here rather than on their own pages: this is the
 * screen that replaces the workbook they used to open, and in that workbook
 * they clicked a cell and typed. So a click on any cell puts that row into edit
 * mode with the clicked cell focused, "Add row" opens a blank row at the top,
 * Enter saves, Escape cancels, and Tab walks the row.
 *
 * One save per row, not per cell: a row is the unit the API validates and the
 * unit a person thinks in ("I filled in this row"). Saving each cell as it lost
 * focus would mean a half-typed row hitting a NOT NULL column and a validation
 * error appearing mid-sentence.
 */
export function RecordTable({ slug, initial }: { slug: string; initial: RecordPage }) {
  const [page, setPage] = useState(initial);
  // Carried explicitly in every request. Leaving it out let the API apply its
  // own default (50) to a table first rendered at 25, so "Next" asked for rows
  // 51-100 of 45 and got an empty page -- and `lastPage` then computed to 1,
  // which hid the pager that would have got you back.
  const [pageSize] = useState(initial.page_size);
  const [orderBy, setOrderBy] = useState<string | undefined>();
  const [desc, setDesc] = useState(false);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Editing state. `editingId` is the row being edited; `adding` is the blank
  // row. Only one of the two is ever active.
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [adding, setAdding] = useState(false);
  const [saving, setSaving] = useState(false);
  // State, not a ref: which cell autofocuses is part of what gets rendered, and
  // a ref read during render is not reactive.
  const [focusColumn, setFocusColumn] = useState<string | null>(null);

  const columns = page.columns;

  const load = useCallback(
    async (opts: Parameters<typeof listRecords>[1]) => {
      setBusy(true);
      setError(null);
      try {
        setPage(await listRecords(slug, opts));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [slug],
  );

  const reload = useCallback(
    () => load({ orderBy, desc, filters, pageSize, page: page.page }),
    [load, orderBy, desc, filters, pageSize, page.page],
  );

  function sort(column: ColumnMeta) {
    if (editingId !== null || adding) return; // don't reorder under an open editor
    const nextDesc = orderBy === column.column_slug ? !desc : false;
    setOrderBy(column.column_slug);
    setDesc(nextDesc);
    void load({ orderBy: column.column_slug, desc: nextDesc, filters, pageSize, page: 1 });
  }

  function filter(slug_: string, value: string) {
    const next = { ...filters, [slug_]: value };
    setFilters(next);
    void load({ orderBy, desc, filters: next, pageSize, page: 1 });
  }

  function startEdit(row: RecordRow, column?: string) {
    setAdding(false);
    setError(null);
    setEditingId(Number(row.id));
    setDraft(toDraft(columns, row));
    setFocusColumn(column ?? columns[0]?.column_slug ?? null);
  }

  function startAdd() {
    setEditingId(null);
    setError(null);
    setAdding(true);
    setDraft(toDraft(columns));
    setFocusColumn(columns[0]?.column_slug ?? null);
  }

  function cancel() {
    setAdding(false);
    setEditingId(null);
    setDraft({});
    setError(null);
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const payload = toPayload(columns, draft);
      if (adding) {
        await createRecord(slug, payload);
      } else if (editingId !== null) {
        await updateRecord(editingId, payload);
      }
      cancel();
      await reload();
    } catch (e) {
      // Kept open on failure: the typed values are still there to correct.
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function remove(row: RecordRow) {
    if (!window.confirm("Delete this row? This cannot be undone.")) return;
    setBusy(true);
    setError(null);
    try {
      await deleteRecord(Number(row.id));
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function keys(ev: React.KeyboardEvent) {
    if (ev.key === "Enter") {
      ev.preventDefault();
      void save();
    } else if (ev.key === "Escape") {
      ev.preventDefault();
      cancel();
    }
  }

  /**
   * One editable cell, as a function returning JSX rather than a nested
   * component. A component *defined* during render is a new type on every
   * render, so React would unmount and remount the input after each keystroke
   * and the caret would jump out of the cell. Inlined elements keep their
   * identity.
   *
   * Autofocuses the cell the user actually clicked.
   */
  function cellInput(column: ColumnMeta) {
    const value = draft[column.column_slug];
    const shared = {
      autoFocus: focusColumn === column.column_slug,
      onKeyDown: keys,
      "aria-label": column.name,
      disabled: saving,
    };
    if (column.data_type === "boolean") {
      return (
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(ev) =>
            setDraft((d) => ({ ...d, [column.column_slug]: ev.target.checked }))
          }
          {...shared}
        />
      );
    }
    return (
      <input
        type={INPUT_TYPE[column.data_type]}
        // numeric columns accept decimals; the default step is 1
        step={column.data_type === "numeric" ? "any" : undefined}
        value={String(value ?? "")}
        onChange={(ev) =>
          setDraft((d) => ({ ...d, [column.column_slug]: ev.target.value }))
        }
        className="w-full min-w-[6rem] rounded border border-neutral-400 bg-white px-1.5 py-1 text-sm dark:border-neutral-600 dark:bg-neutral-950"
        {...shared}
      />
    );
  }

  function rowActions() {
    return (
      <div className="flex justify-end gap-1">
        <button
          onClick={() => void save()}
          disabled={saving}
          className="rounded bg-neutral-900 px-2 py-1 text-xs font-medium text-white disabled:opacity-40 dark:bg-white dark:text-neutral-900"
        >
          {saving ? "Saving…" : "Save"}
        </button>
        <button
          onClick={cancel}
          disabled={saving}
          className="rounded border border-neutral-300 px-2 py-1 text-xs dark:border-neutral-700"
        >
          Cancel
        </button>
      </div>
    );
  }

  const lastPage = Math.max(1, Math.ceil(page.total / page.page_size));
  const editingRow = adding || editingId !== null;

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-w-0">
        <div className="mb-3 flex items-center gap-3">
          <span className="text-sm text-neutral-500">
            {page.total} record{page.total === 1 ? "" : "s"}
          </span>
          {busy && <span className="text-xs text-neutral-400">loading…</span>}
          <button
            onClick={startAdd}
            disabled={editingRow}
            className="ml-auto rounded bg-neutral-900 px-3 py-1.5 text-sm text-white hover:bg-neutral-700 disabled:opacity-40 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200"
          >
            + Add row
          </button>
        </div>

        {error && (
          <p
            role="alert"
            className="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300"
          >
            {error}
          </p>
        )}

        <div className="overflow-x-auto rounded border border-neutral-200 dark:border-neutral-800">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left dark:bg-neutral-900">
              <tr>
                {columns.map((c) => (
                  <th key={c.column_slug} className="px-3 py-2 font-medium">
                    <button
                      onClick={() => sort(c)}
                      className="flex items-center gap-1 text-xs uppercase tracking-wide text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
                    >
                      {c.name}
                      {c.is_primary_key && <span title="Identifies each row">🔑</span>}
                      {orderBy === c.column_slug && <span>{desc ? "↓" : "↑"}</span>}
                    </button>
                  </th>
                ))}
                <th className="px-3 py-2" />
              </tr>
              <tr>
                {columns.map((c) => (
                  <th key={c.column_slug} className="px-3 pb-2">
                    <input
                      aria-label={`Filter ${c.name}`}
                      placeholder="filter…"
                      defaultValue={filters[c.column_slug] ?? ""}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") filter(c.column_slug, e.currentTarget.value);
                      }}
                      onBlur={(e) => {
                        if ((filters[c.column_slug] ?? "") !== e.target.value)
                          filter(c.column_slug, e.target.value);
                      }}
                      className="w-full rounded border border-neutral-200 px-1.5 py-1 text-xs font-normal dark:border-neutral-800 dark:bg-neutral-950"
                    />
                  </th>
                ))}
                <th />
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
              {/* The blank row, at the top where it was just asked for. */}
              {adding && (
                <tr className="bg-emerald-50/60 dark:bg-emerald-950/20">
                  {columns.map((c) => (
                    <td key={c.column_slug} className="px-3 py-1.5">
                      {cellInput(c)}
                    </td>
                  ))}
                  <td className="px-3 py-1.5">
                    {rowActions()}
                  </td>
                </tr>
              )}

              {page.rows.length === 0 && !adding ? (
                <tr>
                  <td
                    colSpan={columns.length + 1}
                    className="px-3 py-8 text-center text-sm text-neutral-500"
                  >
                    No records match.
                  </td>
                </tr>
              ) : (
                page.rows.map((row) => {
                  const isEditing = editingId === Number(row.id);
                  return (
                    <tr
                      key={String(row.id)}
                      className={
                        isEditing
                          ? "bg-neutral-50 dark:bg-neutral-900"
                          : "hover:bg-neutral-50 dark:hover:bg-neutral-900"
                      }
                    >
                      {columns.map((c) => (
                        <td
                          key={c.column_slug}
                          onClick={
                            isEditing || editingRow
                              ? undefined
                              : () => startEdit(row, c.column_slug)
                          }
                          title={isEditing ? undefined : "Click to edit"}
                          className={
                            isEditing
                              ? "px-3 py-1.5"
                              : "max-w-[18rem] cursor-text truncate px-3 py-2"
                          }
                        >
                          {isEditing ? cellInput(c) : display(row[c.column_slug])}
                        </td>
                      ))}
                      <td className="whitespace-nowrap px-3 py-1.5 text-right">
                        {isEditing ? (
                          rowActions()
                        ) : (
                          <button
                            onClick={() => void remove(row)}
                            disabled={editingRow || busy}
                            aria-label={`Delete row ${row.id}`}
                            title="Delete this row"
                            className="rounded px-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-red-600 disabled:opacity-40 dark:hover:bg-neutral-800"
                          >
                            ✕
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
          <p className="text-xs text-neutral-500">
            Click any cell to edit its row. Enter saves, Escape cancels.
          </p>
          {/*
            Shown whenever there is more than one page -- or whenever you are
            not on the first one, so a page that turns out to be empty can
            always be navigated away from.
          */}
          {(lastPage > 1 || page.page > 1) && (
            <div className="ml-auto flex items-center gap-3 text-sm">
              <button
                disabled={page.page <= 1 || editingRow}
                onClick={() =>
                  load({ orderBy, desc, filters, pageSize, page: page.page - 1 })
                }
                className="rounded border border-neutral-300 px-2 py-1 disabled:opacity-40 dark:border-neutral-700"
              >
                Previous
              </button>
              <span className="text-neutral-500">
                Page {page.page} of {lastPage}
              </span>
              <button
                disabled={page.page >= lastPage || editingRow}
                onClick={() =>
                  load({ orderBy, desc, filters, pageSize, page: page.page + 1 })
                }
                className="rounded border border-neutral-300 px-2 py-1 disabled:opacity-40 dark:border-neutral-700"
              >
                Next
              </button>
            </div>
          )}
        </div>

        {/*
          Below the pager on purpose: the notes and totals belong to the table
          as a whole, not to whichever page of rows you are looking at, and
          putting the page controls under them read as if they paged the notes.
        */}
        <Sections sections={page.sections} />

      </div>

      <TableChat slug={slug} entity={page.entity} onApplied={() => void reload()} />
    </div>
  );
}
