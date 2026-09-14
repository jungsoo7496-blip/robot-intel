"use client";

import { useState, useTransition } from "react";

import { submitErrorReport } from "./actions";

const REPORT_TYPES = [
  "사실 오류",
  "잘못된 출처",
  "중복",
  "잘못된 분류",
  "과도한 해석",
  "부적절한 KIRO 시사점",
  "기타",
];

/** 오류 신고 폼 (FR-014). */
export function ErrorReportForm({ publishedItemId }: { publishedItemId: string }) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  if (!open) {
    return (
      <div>
        {result ? (
          <p className="text-sm text-green-700 dark:text-green-400">{result}</p>
        ) : (
          <button
            onClick={() => setOpen(true)}
            className="text-sm text-black/50 underline dark:text-white/50"
          >
            이 동향에서 오류를 발견하셨나요? 오류 신고
          </button>
        )}
      </div>
    );
  }

  return (
    <form
      action={(formData) => {
        startTransition(async () => {
          const res = await submitErrorReport(formData);
          setResult(res.message);
          if (res.ok) setOpen(false);
        });
      }}
      className="space-y-2 rounded-lg border border-black/15 p-4 dark:border-white/20"
    >
      <input type="hidden" name="published_item_id" value={publishedItemId} />
      {/* 허니팟 — 화면에 보이지 않으며 봇만 채운다 */}
      <input
        type="text"
        name="website"
        tabIndex={-1}
        autoComplete="off"
        aria-hidden="true"
        className="hidden"
      />
      <div className="text-sm font-medium">오류 신고</div>
      <select
        name="report_type"
        required
        className="w-full rounded border border-black/20 bg-transparent px-2 py-1.5 text-sm dark:border-white/25 dark:[&>option]:bg-neutral-900"
      >
        <option value="">신고 유형 선택</option>
        {REPORT_TYPES.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>
      <textarea
        name="description"
        rows={3}
        placeholder="설명 (선택)"
        className="w-full rounded border border-black/20 bg-transparent px-2 py-1.5 text-sm dark:border-white/25"
      />
      {result && <p className="text-sm text-red-600">{result}</p>}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={pending}
          className="rounded bg-foreground px-3 py-1.5 text-sm font-medium text-background disabled:opacity-50"
        >
          {pending ? "제출 중…" : "제출"}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded border border-black/20 px-3 py-1.5 text-sm dark:border-white/25"
        >
          취소
        </button>
      </div>
    </form>
  );
}
