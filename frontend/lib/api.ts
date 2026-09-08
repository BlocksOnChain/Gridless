/**
 * Typed fetch client for the Gridless backend.
 *
 * v1 has no auth. Every request carries the organization in the `X-Org-Id`
 * header; the browser's EventSource cannot set headers, so SSE endpoints take
 * `org_id` as a query parameter instead (see `sseUrl`).
 */

/**
 * Where the browser reaches the API. Baked into the client bundle at build
 * time, so it has to be a URL that resolves on the user's machine -- under
 * Docker Compose that is the published port, never the `api` service name.
 */
export const PUBLIC_API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8010";

/**
 * Where *this process* reaches the API when rendering on the server.
 *
 * Inside a container `127.0.0.1` is the container itself, so server components
 * would fetch nothing. `API_INTERNAL_BASE` (set to http://api:8010 by Compose)
 * is read at runtime and falls back to the public URL, which is what makes
 * `pnpm dev` on a laptop work unchanged.
 */
const INTERNAL_API_BASE = process.env.API_INTERNAL_BASE || PUBLIC_API_BASE;

export const API_BASE =
  typeof window === "undefined" ? INTERNAL_API_BASE : PUBLIC_API_BASE;

export const ORG_ID = process.env.NEXT_PUBLIC_ORG_ID ?? "1";

/**
 * Resolve a fetch to a value instead of throwing.
 *
 * Server components cannot return JSX from a catch block (React wants an error
 * boundary), and "this workbook has not been committed yet" is a legitimate
 * state rather than an error, so it is modelled as one.
 */
export async function attempt<T>(
  fn: () => Promise<T>,
): Promise<{ ok: true; value: T } | { ok: false; error: string }> {
  try {
    return { ok: true, value: await fn() };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(`API ${status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Org-Id", ORG_ID);
  if (init.body !== undefined && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers, cache: "no-store" });

  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      detail = await res.text();
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
    }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

/**
 * Build an SSE URL, passing the org as a query param since EventSource cannot
 * set headers. Always the public base: EventSource runs in the browser.
 */
export function sseUrl(path: string, params: Record<string, string> = {}): string {
  const url = new URL(`${PUBLIC_API_BASE}${path}`);
  url.searchParams.set("org_id", ORG_ID);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  return url.toString();
}

// --- Response types (mirror the backend's msgspec.Struct definitions) -------

export interface Health {
  status: string;
  database: string;
}

export interface Workbook {
  id: number;
  original_filename: string;
  status: "uploaded" | "analysing" | "proposed" | "committed" | "failed";
  uploaded_at: string;
  sheet_count: number;
  entity_count: number;
  error: string;
}

export interface AnalyseResult {
  workbook_id: number;
  status: string;
  sheets: number;
  entities: number;
  fields: number;
  relationships: number;
  low_confidence: string[];
}

export const getHealth = () => api.get<Health>("/api/health");

export const listWorkbooks = () => api.get<Workbook[]>("/api/workbooks");

export function uploadWorkbook(file: File): Promise<Workbook> {
  const form = new FormData();
  form.append("file", file);
  return api.post<Workbook>("/api/workbooks", form);
}

export const analyseWorkbook = (id: number) =>
  api.post<AnalyseResult>(`/api/workbooks/${id}/analyse`);

// --- Proposal (the review screen) ------------------------------------------

export type DataType =
  | "text" | "integer" | "numeric" | "date" | "datetime" | "boolean" | "json";

export const DATA_TYPES: DataType[] = [
  "text", "integer", "numeric", "date", "datetime", "boolean", "json",
];

export interface Sheet {
  id: number;
  name: string;
  detected_header_row: number | null;
  detected_range: string;
  raw_row_count: number;
  notes: string;
}

export interface Field {
  id: number;
  name: string;
  source_header: string;
  column_slug: string;
  source_column_letter: string;
  data_type: DataType;
  nullable: boolean;
  is_primary_key: boolean;
  confidence: number;
  user_confirmed: boolean;
  sample_values: unknown[];
  position: number;
}

/** Kinds of section a table can carry. */
export type SectionKind = "note" | "summary" | "meta";

/**
 * A piece of a sheet that belongs with a table but is not rows of it: an
 * AÇIKLAMALAR block, a totals line, a report date. Rendered under the table.
 */
export interface Section {
  id: number;
  kind: SectionKind;
  title: string;
  body: string;
  source_range: string;
  position: number;
  /** True when a person or the assistant wrote it, so re-analysis keeps it. */
  created_by_user: boolean;
}

export interface Entity {
  id: number;
  name: string;
  table_slug: string;
  confidence: number;
  user_confirmed: boolean;
  sheet: Sheet;
  fields: Field[];
  sections: Section[];
}

export interface Relationship {
  id: number;
  name: string;
  from_entity_id: number;
  from_entity: string;
  from_field_id: number;
  from_field: string;
  to_entity_id: number;
  to_entity: string;
  to_field_id: number;
  to_field: string;
  cardinality: string;
  match_rate: number;
  confidence: number;
  user_confirmed: boolean;
  rejected: boolean;
  /** The ranking stage returned no verdict: neither accepted nor rejected. */
  needs_review: boolean;
  rationale: string;
}

export interface Proposal {
  workbook: Workbook;
  entities: Entity[];
  relationships: Relationship[];
}

export const getProposal = (id: number) =>
  api.get<Proposal>(`/api/workbooks/${id}/proposal`);

export const patchEntity = (id: number, patch: Partial<Pick<Entity, "name" | "table_slug" | "user_confirmed">>) =>
  api.patch<Entity>(`/api/entities/${id}`, patch);

export const patchField = (
  id: number,
  patch: Partial<Pick<Field, "name" | "column_slug" | "data_type" | "is_primary_key" | "user_confirmed">>,
) => api.patch<Field>(`/api/fields/${id}`, patch);

export const patchRelationship = (
  id: number,
  patch: Partial<Pick<Relationship, "user_confirmed" | "rejected">>,
) => api.patch<Relationship>(`/api/relationships/${id}`, patch);

/** Add a column the spreadsheet did not have. It commits empty. */
export const addField = (entityId: number, body: { name: string; data_type?: DataType }) =>
  api.post<Field>(`/api/entities/${entityId}/fields`, body);

/** Drop a column from the proposal. The uploaded file is untouched. */
export const deleteField = (id: number) => api.delete<void>(`/api/fields/${id}`);

// --- Sections --------------------------------------------------------------

export const addSection = (
  entityId: number,
  body: { title: string; body: string; kind?: SectionKind },
) => api.post<Section>(`/api/entities/${entityId}/sections`, body);

export const patchSection = (
  id: number,
  patch: Partial<Pick<Section, "title" | "body" | "kind">>,
) => api.patch<Section>(`/api/sections/${id}`, patch);

export const deleteSection = (id: number) => api.delete<void>(`/api/sections/${id}`);

// --- Review chat -----------------------------------------------------------

export interface AssistTurn {
  role: "user" | "assistant";
  content: string;
}

export interface AssistResponse {
  reply: string;
  /**
   * One line per change that was actually applied. The reply is the model's
   * account of what it did; this is the record of what happened. When they
   * disagree, this is the one to believe -- and to show.
   */
  changes: string[];
  /** The proposal re-read afterwards, so the screen never guesses. */
  proposal: Proposal;
}

export const assist = (workbookId: number, message: string, history: AssistTurn[] = []) =>
  api.post<AssistResponse>(`/api/workbooks/${workbookId}/assist`, { message, history });

// --- Commit ----------------------------------------------------------------

export interface Preflight {
  can_commit: boolean;
  problems: string[];
}

export interface CommitResult {
  workbook_id: number;
  status: string;
  schema: string;
  entities: number;
  records: number;
  views: string[];
  edges: number;
  skipped_relationships: string[];
}

export const getPreflight = (id: number) =>
  api.get<Preflight>(`/api/workbooks/${id}/preflight`);

export const commitWorkbook = (id: number) =>
  api.post<CommitResult>(`/api/workbooks/${id}/commit`);

// --- Committed data (the generated app) ------------------------------------

export interface ColumnMeta {
  name: string;
  column_slug: string;
  data_type: DataType;
  nullable: boolean;
  is_primary_key: boolean;
}

export interface EntityMeta {
  id: number;
  name: string;
  table_slug: string;
  workbook: string;
  record_count: number;
  columns: ColumnMeta[];
}

export type RecordRow = Record<string, unknown>;

/** The section shape the generated app needs: no provenance, just content. */
export interface SectionMeta {
  id: number;
  kind: SectionKind;
  title: string;
  body: string;
}

export interface RecordPage {
  entity: string;
  table_slug: string;
  columns: ColumnMeta[];
  total: number;
  page: number;
  page_size: number;
  rows: RecordRow[];
  sections: SectionMeta[];
}

export interface RecordOut {
  id: number;
  entity_slug: string;
  data: RecordRow;
}

export const listEntities = () => api.get<EntityMeta[]>("/api/entities");

export function listRecords(
  slug: string,
  opts: {
    page?: number;
    pageSize?: number;
    orderBy?: string;
    desc?: boolean;
    filters?: Record<string, string>;
  } = {},
) {
  // Built with encodeURIComponent rather than URLSearchParams on purpose.
  // URLSearchParams encodes a space as `+`, which only means "space" under the
  // form-urlencoded convention -- and the API's query parser percent-decodes
  // without applying it, so filtering for `Tuna Ilhan` searched for
  // `Tuna+Ilhan` and matched nothing. `%20` is unambiguous, and a value that
  // genuinely contains a plus still round-trips as `%2B`.
  const parts: string[] = [];
  const put = (key: string, value: string) =>
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`);

  if (opts.page) put("page", String(opts.page));
  if (opts.pageSize) put("page_size", String(opts.pageSize));
  if (opts.orderBy) put("order_by", opts.orderBy);
  if (opts.desc) put("desc", "true");
  for (const [k, v] of Object.entries(opts.filters ?? {})) if (v !== "") put(k, v);

  const qs = parts.join("&");
  return api.get<RecordPage>(`/api/entities/${slug}/records${qs ? `?${qs}` : ""}`);
}

export const createRecord = (slug: string, data: RecordRow) =>
  api.post<RecordOut>(`/api/entities/${slug}/records`, data);

export const updateRecord = (id: number, data: RecordRow) =>
  api.patch<RecordOut>(`/api/records/${id}`, data);

export const deleteRecord = (id: number) => api.delete<void>(`/api/records/${id}`);

// --- Bulk edits and the table chat -----------------------------------------

/**
 * Comparisons the backend can compile. `is_empty` / `is_not_empty` take no
 * value; the rest take one, coerced server-side by the column's type.
 */
export type BulkOperator =
  | "eq" | "ne" | "lt" | "lte" | "gt" | "gte"
  | "contains" | "starts_with" | "is_empty" | "is_not_empty";

export interface BulkCondition {
  column: string;
  operator: BulkOperator;
  value?: unknown;
}

/**
 * What to do, to which rows.
 *
 * A plan is produced by the chat, shown to the user, and posted back only if
 * they confirm it. The server re-validates on the way back in -- the browser
 * confirms, it does not authorise.
 */
export interface BulkPlan {
  op: "delete" | "update" | "count";
  where: BulkCondition[];
  changes: RecordRow;
  /** Required for a plan with no conditions, i.e. one that hits every row. */
  match_all: boolean;
}

export interface BulkPreview {
  summary: string;
  matched: number;
  total: number;
  columns: string[];
  sample: RecordRow[];
  affects_everything: boolean;
}

export interface BulkResult {
  op: string;
  affected: number;
  summary: string;
}

export interface TableAssistResponse {
  reply: string;
  /** Non-null when the assistant prepared a change. Nothing has been applied. */
  plan: BulkPlan | null;
  preview: BulkPreview | null;
  /** Read-only counts it looked up while answering. */
  lookups: string[];
}

export const tableAssist = (
  slug: string,
  message: string,
  history: AssistTurn[] = [],
) =>
  api.post<TableAssistResponse>(`/api/entities/${slug}/assist`, { message, history });

export const previewBulk = (slug: string, plan: BulkPlan) =>
  api.post<BulkPreview>(`/api/entities/${slug}/bulk/preview`, plan);

/** Runs the plan. Irreversible: only call this on an explicit confirmation. */
export const applyBulk = (slug: string, plan: BulkPlan) =>
  api.post<BulkResult>(`/api/entities/${slug}/bulk`, plan);


// --- Ask -------------------------------------------------------------------

export interface AskResponse {
  question: string;
  answer: string;
  sql: string;
  columns: string[];
  rows: RecordRow[];
  row_count: number;
  truncated: boolean;
  attempts: number;
}

export const ask = (question: string) => api.post<AskResponse>("/api/ask", { question });
