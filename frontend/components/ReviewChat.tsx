"use client";

import { useRef, useState } from "react";
import { assist, type AssistTurn, type Proposal } from "@/lib/api";

/**
 * The review screen's assistant.
 *
 * It changes the columns rather than talking about them: the server applies
 * every change with a tool and hands back both the list of changes it actually
 * made and the re-read proposal, which is what `onProposal` pushes into the
 * screen. So the table always shows the database, never the model's account of
 * it -- and a reply that overstates what happened is contradicted on screen by
 * the change list next to it.
 */

const EXAMPLES = [
  "Joined should be a date",
  "Add a column for the renewal owner",
  "Rename Full Name to Customer Name",
];

type Message = { role: "user" | "assistant"; content: string; changes?: string[] };

export function ReviewChat({
  workbookId,
  onProposal,
}: {
  workbookId: number;
  onProposal: (proposal: Proposal) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  async function send(text: string) {
    const message = text.trim();
    if (!message || sending) return;

    // Only the visible conversation is replayed as history; the server drops
    // its own tool traffic for the same reason.
    const history: AssistTurn[] = messages.map((m) => ({
      role: m.role,
      content: m.content,
    }));

    setMessages((prev) => [...prev, { role: "user", content: message }]);
    setInput("");
    setSending(true);
    setError(null);
    requestAnimationFrame(() => endRef.current?.scrollIntoView({ behavior: "smooth" }));

    try {
      const result = await assist(workbookId, message, history);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: result.reply, changes: result.changes },
      ]);
      onProposal(result.proposal);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
      requestAnimationFrame(() => endRef.current?.scrollIntoView({ behavior: "smooth" }));
    }
  }

  return (
    <section
      aria-label="Assistant"
      className="flex h-[calc(100vh-14rem)] min-h-[24rem] flex-col rounded-lg border border-neutral-200 dark:border-neutral-800"
    >
      <header className="border-b border-neutral-200 px-4 py-3 dark:border-neutral-800">
        <h2 className="text-sm font-medium">Ask for a change</h2>
        <p className="mt-0.5 text-xs text-neutral-500">
          Describe it in your own words — the columns update as you go.
        </p>
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {messages.length === 0 && (
          <div className="space-y-2">
            {EXAMPLES.map((example) => (
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
          <div
            key={i}
            className={m.role === "user" ? "flex justify-end" : "flex justify-start"}
          >
            <div
              className={`max-w-[90%] rounded-lg px-3 py-2 text-sm ${
                m.role === "user"
                  ? "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900"
                  : "bg-neutral-100 text-neutral-800 dark:bg-neutral-900 dark:text-neutral-200"
              }`}
            >
              <p className="whitespace-pre-wrap">{m.content}</p>
              {m.role === "assistant" && m.changes !== undefined && (
                <ul className="mt-2 space-y-1 border-t border-neutral-200 pt-2 text-xs dark:border-neutral-700">
                  {m.changes.length === 0 ? (
                    <li className="text-neutral-500">Nothing was changed.</li>
                  ) : (
                    m.changes.map((change) => (
                      <li key={change} className="flex gap-1.5 text-emerald-700 dark:text-emerald-400">
                        <span aria-hidden>✓</span>
                        <span>{change}</span>
                      </li>
                    ))
                  )}
                </ul>
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
          // Explicit rather than relying on the form's implicit submission:
          // Enter is how people send a chat message, and it should not depend
          // on which element the browser thinks is the default button.
          onKeyDown={(ev) => {
            if (ev.key === "Enter" && !ev.shiftKey) {
              ev.preventDefault();
              void send(input);
            }
          }}
          placeholder="e.g. add a column for the owner"
          aria-label="Ask for a change"
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
