/**
 * Turning a typed column into an editable cell, and back.
 *
 * One place, because the table's inline editing has to agree with what the API
 * will accept: an `<input type="number">` hands back a string, a checkbox a
 * boolean, and an empty box means null rather than "". Getting that wrong shows
 * up as a 400 from a column the user never typed into.
 */
import type { ColumnMeta, DataType, RecordRow } from "@/lib/api";

export const INPUT_TYPE: Record<DataType, string> = {
  text: "text",
  integer: "number",
  numeric: "number",
  date: "date",
  datetime: "datetime-local",
  boolean: "checkbox",
  json: "text",
};

/** A stored value as the matching input wants to receive it. */
export function toInputValue(value: unknown, type: DataType): string {
  if (value === null || value === undefined) return "";
  // <input type="datetime-local"> rejects a value with seconds or a zone.
  if (type === "datetime") return String(value).slice(0, 16);
  if (type === "json") return typeof value === "string" ? value : JSON.stringify(value);
  return String(value);
}

export type Draft = Record<string, string | boolean>;

/** Every column of a row, as input state. */
export function toDraft(columns: ColumnMeta[], row?: RecordRow): Draft {
  return Object.fromEntries(
    columns.map((c) => [
      c.column_slug,
      c.data_type === "boolean"
        ? Boolean(row?.[c.column_slug])
        : toInputValue(row?.[c.column_slug], c.data_type),
    ]),
  );
}

/** Input state as the API wants it: booleans real, blanks null. */
export function toPayload(columns: ColumnMeta[], draft: Draft): RecordRow {
  const payload: RecordRow = {};
  for (const c of columns) {
    const value = draft[c.column_slug];
    payload[c.column_slug] =
      c.data_type === "boolean" ? Boolean(value) : value === "" ? null : value;
  }
  return payload;
}

/** How a value reads in a non-editing cell. */
export function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}
