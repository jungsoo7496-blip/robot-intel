"use client";

import { useActionState } from "react";

import { saveNewsSearchTarget } from "./actions";
import {
  DISPLAY_MAX,
  DISPLAY_MIN,
  INTERVAL_MAX,
  INTERVAL_MIN,
  PRIORITY_MAX,
  PRIORITY_MIN,
  TARGET_INFO,
  type NewsSearchTargetRow,
} from "./keyword-fields";

/*
 * 검색 대상 설정 카드 (요구 1-1 — 우선순위·수집 간격을 검색어마다가 아니라
 * 대상별로 한 번에 정한다).
 */

const inputClass =
  "w-full rounded border border-black/20 bg-transparent px-3 py-2 text-sm dark:border-white/25";
const labelClass = "block text-xs font-medium text-black/60 dark:text-white/60";
const helpClass = "mt-1 text-xs text-black/50 dark:text-white/50";

export function TargetForm({
  target,
  newsKeywordCount,
  /** 이 대상이 최근 7일 동안 실제로 들여온 기사 수(하루 평균). 없으면 null. */
  perDay,
}: {
  target: NewsSearchTargetRow;
  newsKeywordCount: number;
  perDay: number | null;
}) {
  const [state, formAction, pending] = useActionState(saveNewsSearchTarget, null);
  const info = TARGET_INFO[target.target_key];

  return (
    <form
      action={formAction}
      className="space-y-3 rounded-lg border border-black/10 p-4 dark:border-white/15"
    >
      <input type="hidden" name="target_key" value={target.target_key} />

      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-base font-semibold">{info.title}</h3>
        <span className="text-xs text-black/50 dark:text-white/50">
          수집원 {newsKeywordCount}개
          {perDay !== null && ` · 최근 7일 하루 평균 ${perDay.toLocaleString("ko-KR")}건`}
        </span>
      </div>
      <p className="text-xs text-black/60 dark:text-white/60">{info.what}</p>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          name="is_active"
          defaultChecked={target.is_active}
        />
        이 대상에서 검색합니다
      </label>

      <div
        className={`grid gap-3 ${info.usesDisplay ? "sm:grid-cols-3" : "sm:grid-cols-2"}`}
      >
        <div>
          <label className={labelClass} htmlFor={`t-${target.target_key}-priority`}>
            우선순위
          </label>
          <input
            id={`t-${target.target_key}-priority`}
            name="priority"
            type="number"
            min={PRIORITY_MIN}
            max={PRIORITY_MAX}
            defaultValue={target.priority}
            className={inputClass}
          />
          <p className={helpClass}>수집 순서만 정합니다(낮을수록 먼저).</p>
        </div>
        <div>
          <label className={labelClass} htmlFor={`t-${target.target_key}-interval`}>
            수집 간격(분)
          </label>
          <input
            id={`t-${target.target_key}-interval`}
            name="fetch_interval_minutes"
            type="number"
            min={INTERVAL_MIN}
            max={INTERVAL_MAX}
            defaultValue={target.fetch_interval_minutes}
            className={inputClass}
          />
          <p className={helpClass}>
            마지막 성공에서 이 시간이 지나야 다시 가져옵니다. 수집이 하루 3회라
            8시간보다 짧게 잡으면 사실상 매번 가져옵니다.
          </p>
        </div>
        {info.usesDisplay && (
          <div>
            <label className={labelClass} htmlFor={`t-${target.target_key}-display`}>
              조회 건수
            </label>
            <input
              id={`t-${target.target_key}-display`}
              name="display"
              type="number"
              min={DISPLAY_MIN}
              max={DISPLAY_MAX}
              step={10}
              defaultValue={target.display}
              className={inputClass}
            />
            <p className={helpClass}>
              검색어 하나당 한 번에 볼 최신 기사 수({DISPLAY_MIN}~{DISPLAY_MAX}).
              유입이 너무 많으면 {DISPLAY_MIN}으로 낮추세요.
            </p>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={pending}
          className="rounded bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
        >
          {pending ? "저장 중…" : "저장"}
        </button>
        {state && (
          <p
            aria-live="polite"
            className={`text-sm ${
              state.ok ? "text-green-700 dark:text-green-400" : "text-red-600"
            }`}
          >
            {state.message}
          </p>
        )}
      </div>
    </form>
  );
}
