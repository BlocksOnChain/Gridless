"use client";

import { useState } from "react";
import Link from "next/link";
import { uploadWorkbook, type Workbook } from "@/lib/api";

interface Outcome {
  filename: string;
  workbook?: Workbook;
  error?: string;
}

export default function UploadPage() {
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);

  async function send(files: FileList | File[]) {
    setBusy(true);
    const results: Outcome[] = [];
    // One request per file so a single bad workbook cannot fail the batch.
    for (const file of Array.from(files)) {
      try {
        results.push({ filename: file.name, workbook: await uploadWorkbook(file) });
      } catch (e) {
        results.push({
          filename: file.name,
          error: e instanceof Error ? e.message : String(e),
        });
      }
    }
    setOutcomes((prev) => [...results, ...prev]);
    setBusy(false);
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <Link href="/" className="text-sm text-neutral-500 underline underline-offset-2">
        ← Workbooks
      </Link>
      <h1 className="mt-4 text-2xl font-semibold tracking-tight">Upload workbooks</h1>
      <p className="mt-2 text-sm text-neutral-500">
        Drop one or more <code className="font-mono">.xlsx</code> files. Each is uploaded
        separately, so one bad file does not fail the rest.
      </p>

      <label
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (e.dataTransfer.files.length) void send(e.dataTransfer.files);
        }}
        className={`mt-8 flex h-44 cursor-pointer items-center justify-center rounded-lg border-2 border-dashed text-sm transition-colors ${
          dragging
            ? "border-blue-400 bg-blue-50 dark:bg-blue-950"
            : "border-neutral-300 dark:border-neutral-700"
        }`}
      >
        <input
          type="file"
          accept=".xlsx"
          multiple
          className="hidden"
          disabled={busy}
          onChange={(e) => {
            if (e.target.files?.length) void send(e.target.files);
            e.target.value = "";
          }}
        />
        <span className="text-neutral-500">
          {busy ? "Uploading…" : "Drag .xlsx files here, or click to choose"}
        </span>
      </label>

      {outcomes.length > 0 && (
        <ul className="mt-8 divide-y divide-neutral-200 text-sm dark:divide-neutral-800">
          {outcomes.map((o, i) => (
            <li key={`${o.filename}-${i}`} className="py-2">
              <span className="font-medium">{o.filename}</span>{" "}
              {o.workbook ? (
                <span className="text-emerald-600 dark:text-emerald-400">
                  uploaded (id {o.workbook.id})
                </span>
              ) : (
                <span className="font-mono text-xs text-red-600 dark:text-red-400">
                  {o.error}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
