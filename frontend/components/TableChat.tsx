"use client";

import { useRef, useState } from "react";
import {
  applyBulk,
  tableAssist,
  type AssistTurn,
  type BulkPlan,
  type BulkPreview,
  type RecordRow,
} from "@/lib/api";
import { display } from "@/lib/cells";

/**
 * Change many rows by asking.
 *
 * The assistant cannot change anything. It prepares a plan -- what operation,
 * on which rows -- and the server answers with how many of how many rows it
 * matches plus a sample of them. Applying is a separate call this component
 * makes only when the person presses Apply, having read that.
 *
 * That extra click is the point, not friction to be optimised away: "delete 12
 * records" is irreversible, and a number and five example rows are the only
 * honest way to ask someone whether they meant it.
 */

type Message = {
  role: "user" | "assistant";
  content: string;
  plan?: BulkPlan | null;
  preview?: BulkPreview | null;
  lookups?: string[];
  /** Set once the plan has been applied, so it cannot be applied twice. */
  applied?: string;
};

export function TableChat({
  slug,
  entity,
  onApplied,
}: {
  slug: string;
  entity: string;
  onApplied: () => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [applying, setApplying] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const scroll = () =>
    requestAnimationFrame(() => endRef.current?.scrollIntoView({ behavior: "smooth" }));

  async function send(text: string) {
    const message = text.trim();
    if (!message || sending) return;

    const history: AssistTurn[] = messages.map((m) => ({
      role: m.role,
      content: m.content,
    }));
    setMessages((prev) => [...prev, { role: "user", content: message }]);
    setInput("");
    setSending(true);
    setError(null);
    scroll();

    try {
      const result = await tableAssist(slug, message, history);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: result.reply,
          plan: result.plan,
          preview: result.preview,
          lookups: result.lookups,
        },
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
      scroll();
    }
  }

  async function apply(index: number, plan: BulkPlan) {
    setApplying(index);
    setError(null);
    try {
      const result = await applyBulk(slug, plan);
      setMessages((prev) =>
        prev.map((m, i) => (i === index ? { ...m, applied: result.summary } : m)),
      );
      onApplied();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setApplying(null);
    }
  }

  return (
    <section
      aria-label="Table assistant"
      className="flex h-[36rem] flex-col rounded-lg border border-neutral-200 dark:border-neutral-800"
    >
      <header className="border-b border-neutral-200 px-4 py-3 dark:border-neutral-800">
        <h2 className="text-sm font-medium">Change many rows at once</h2>
        <p className="mt-0.5 text-xs text-neutral-500">
          Describe it — you will see what it affects before anything happens.
        </p>
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {messages.length === 0 && (
          <div className="space-y-2">
            {[
              `How many ${entity} rows are there?`,
              "Delete the rows where Total (KG) is 0",
            ].map((example) => (
              <button
                key={example}
                onClick={() => send(example)}
                disabled={sending}
                className="block w-full rounded border border-dashed border-neutral-300 px-3 py-2 text-left text-xs text-neutral-600 hover:border-neutral-400 hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-900"
              >
                “{example}”
              </button>
            ))}
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
            <div
              className={`max-w-[95%] rounded-lg px-3 py-2 text-sm ${
                m.role === "user"
                  ? "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900"
                  : "bg-neutral-100 text-neutral-800 dark:bg-neutral-900 dark:text-neutral-200"
              }`}
            >
              <p className="whitespace-pre-wrap">{m.content}</p>

              {m.lookups?.map((lookup) => (
                <p key={lookup} className="mt-1 text-xs text-neutral-500">
                  {lookup}
                </p>
              ))}

              {m.plan && m.preview && (
                <PlanCard
                  plan={m.plan}
                  preview={m.preview}
                  applied={m.applied}
                  busy={applying === i}
                  onApply={() => apply(i, m.plan!)}
                  onDismiss={() =>
                    setMessages((prev) =>
                      prev.map((x, j) => (j === i ? { ...x, plan: null } : x)),
                    )
                  }
                />
              )}
            </div>
          </div>
        ))}

        {sending && <p className="text-xs text-neutral-500">Working on it…</p>}
        {error && (
          <p
            role="alert"
            className="rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300"
          >
            {error}
          </p>
        )}
        <div ref={endRef} />
      </div>

      <form
        onSubmit={(ev) => {
          ev.preventDefault();
          void send(input);
        }}
        className="flex gap-2 border-t border-neutral-200 p-3 dark:border-neutral-800"
      >
        <input
          value={input}
          onChange={(ev) => setInput(ev.target.value)}
          onKeyDown={(ev) => {
            if (ev.key === "Enter" && !ev.shiftKey) {
              ev.preventDefault();
              void send(input);
            }
          }}
          placeholder="e.g. delete rows where Total (KG) is 0"
          aria-label="Ask for a change to many rows"
          className="min-w-0 flex-1 rounded border border-neutral-300 px-2 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-950"
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="rounded bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40 dark:bg-white dark:text-neutral-900"
        >
          Send
        </button>
      </form>
    </section>
  );
}

/** The confirmation: what it hits, a sample of it, and one button. */
function PlanCard({
  plan,
  preview,
  applied,
  busy,
  onApply,
  onDismiss,
}: {
  plan: BulkPlan;
  preview: BulkPreview;
  applied?: string;
  busy: boolean;
  onApply: () => void;
  onDismiss: () => void;
}) {
  const destructive = plan.op === "delete";
  const nothingToDo = preview.matched === 0;

  return (
    <div
      className={`mt-2 rounded border p-2 ${
        destructive
          ? "border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/40"
          : "border-amber-300 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/40"
      }`}
    >
      <p className="text-xs font-medium">{preview.summary}</p>

      {preview.affects_everything && !nothingToDo && (
        <p className="mt-1 text-xs font-semibold text-red-700 dark:text-red-400">
          This is every row in the table.
        </p>
      )}

      {preview.sample.length > 0 && (
        <div className="mt-2 max-h-32 overflow-auto rounded border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-950">
          <table className="w-full text-[11px]">
            <tbody>
              {preview.sample.map((row: RecordRow, i) => (
                <tr key={i} className="border-b border-neutral-100 dark:border-neutral-800">
                  {preview.columns
                    .filter((c) => c !== "created_at" && c !== "updated_at")
                    .map((c) => (
                      <td key={c} className="max-w-[8rem] truncate px-1.5 py-1">
                        {display(row[c])}
                      </td>
                    ))}
                </tr>
              ))}
            </tbody>
          </table>
          {preview.matched > preview.sample.length && (
            <p className="px-1.5 py-1 text-[11px] text-neutral-500">
              …and {preview.matched - preview.sample.length} more.
            </p>
          )}
        </div>
      )}

      {applied ? (
        <p className="mt-2 text-xs font-medium text-emerald-700 dark:text-emerald-400">
          ✓ {applied}
        </p>
      ) : (
        <div className="mt-2 flex gap-2">
          <button
            onClick={onApply}
            disabled={busy || nothingToDo}
            className={`rounded px-2 py-1 text-xs font-medium text-white disabled:opacity-40 ${
              destructive ? "bg-red-600 hover:bg-red-700" : "bg-neutral-900 hover:bg-neutral-700 dark:bg-white dark:text-neutral-900"
            }`}
          >
            {busy
              ? "Applying…"
              : nothingToDo
                ? "Nothing to apply"
                : destructive
                  ? `Delete ${preview.matched}`
                  : `Update ${preview.matched}`}
          </button>
          <button
            onClick={onDismiss}
            disabled={busy}
            className="rounded border border-neutral-300 px-2 py-1 text-xs dark:border-neutral-700"
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
