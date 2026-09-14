"use client";

import { useActionState } from "react";

import { regenerateBrief, type RegenerateState } from "./actions";

function ResultMessage({ state }: { state: RegenerateState }) {
  if (!state) return null;
  return (
    <p
      className={`text-xs ${state.ok ? "text-green-700 dark:text-green-400" : "text-red-600"}`}
    >
      {state.message}
    </p>
  );
}

/** 표의 기간 행마다 붙는 '이 주차 다시 생성' 버튼. */
export function RegenerateRowButton({ weekStart }: { weekStart: string }) {
  const [state, formAction, pending] = useActionState(regenerateBrief, null);

  return (
    <form action={formAction} className="space-y-1">
      <input type="hidden" name="week_start" value={weekStart} />
      <button
        type="submit"
        disabled={pending}
        className="whitespace-nowrap rounded border border-black/20 px-2 py-1 text-xs hover:bg-black/5 disabled:opacity-50 dark:border-white/25 dark:hover:bg-white/10"
      >
        {pending ? "요청 중…" : "이 주차 다시 생성"}
      </button>
      <div className="max-w-xs">
        <ResultMessage state={state} />
      </div>
    </form>
  );
}

export type WeekOption = { start: string; end: string; note: string };

/** 주차 지정 생성 폼: 지난 8주 월요일 드롭다운 + 날짜 직접 입력. */
export function SpecificWeekForm({ weeks }: { weeks: WeekOption[] }) {
  const [state, formAction, pending] = useActionState(regenerateBrief, null);

  return (
    <form action={formAction} className="space-y-2">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="mb-1 block text-xs text-black/60 dark:text-white/60">
            주차 고르기 (최근 8주)
          </span>
          <select
            name="week_start"
            className="rounded border border-black/20 bg-transparent px-3 py-2 text-sm dark:border-white/25"
          >
            {weeks.map((w) => (
              <option key={w.start} value={w.start}>
                {w.start} ~ {w.end}
                {w.note ? ` · ${w.note}` : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-xs text-black/60 dark:text-white/60">
            또는 날짜 직접 입력 (이쪽이 우선)
          </span>
          <input
            type="date"
            name="week_start_manual"
            className="rounded border border-black/20 bg-transparent px-3 py-2 text-sm dark:border-white/25"
          />
        </label>
        <button
          type="submit"
          disabled={pending}
          className="rounded bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
        >
          {pending ? "요청 중…" : "이 주차 생성"}
        </button>
      </div>
      <ResultMessage state={state} />
    </form>
  );
}
