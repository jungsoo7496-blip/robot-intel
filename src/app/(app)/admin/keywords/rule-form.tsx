"use client";

import { useActionState, useState } from "react";

import {
  addKeywordRule,
  deleteKeywordRule,
  testNewsFilter,
  updateMinBodyLength,
} from "./actions";
import type { ActionState, FilterTestState, FilterVerdict } from "./actions";

/*
 * 수집 규칙 화면의 클라이언트 조각들 (2026-09-08, 관리 기능).
 * 화면 이름은 '수집 규칙'이지만 표·라우트 이름은 keyword_rules / keywords 그대로다.
 * - RuleForm: 규칙 추가 (useActionState + { ok, message })
 * - RuleChip: 규칙 한 건 — 삭제만 (사용자 요구 3-1: 끄기/켜기 없음)
 * - MinBodyLengthForm: 뉴스 필터 본문 최소 길이
 * - FilterTester: 제목·본문을 넣어 뉴스 필터 판정을 미리 보는 테스터
 */

const inputClass =
  "rounded border border-black/20 bg-transparent px-2 py-1 text-sm dark:border-white/25";
const primaryButtonClass =
  "rounded bg-foreground px-3 py-1 text-sm font-medium text-background disabled:opacity-50";
const smallButtonClass =
  "rounded border border-black/20 px-1.5 py-0 text-[11px] leading-5 hover:bg-black/5 dark:border-white/25 dark:hover:bg-white/10";

function StateMessage({ state }: { state: ActionState }) {
  if (!state) return null;
  return (
    <p
      className={`text-sm ${state.ok ? "text-green-700 dark:text-green-400" : "text-red-600"}`}
    >
      {state.message}
    </p>
  );
}

export type KindOption = { value: string; label: string; regexDefault?: boolean };

/** 규칙 추가 폼 — 용어·정규식 여부·메모. */
export function RuleForm({
  ruleSet,
  kinds,
}: {
  ruleSet: string;
  kinds: KindOption[];
}) {
  const [state, formAction, pending] = useActionState(addKeywordRule, null);
  const [kind, setKind] = useState(kinds[0]?.value ?? "");
  const [isRegex, setIsRegex] = useState(Boolean(kinds[0]?.regexDefault));

  return (
    <form action={formAction} className="space-y-2">
      <input type="hidden" name="rule_set" value={ruleSet} />
      <div className="flex flex-wrap items-center gap-2">
        <select
          name="kind"
          value={kind}
          onChange={(e) => {
            const next = e.target.value;
            setKind(next);
            setIsRegex(Boolean(kinds.find((k) => k.value === next)?.regexDefault));
          }}
          className={inputClass}
          aria-label="규칙 종류"
        >
          {kinds.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
        <input
          type="text"
          name="term"
          required
          maxLength={200}
          placeholder={isRegex ? "정규식 (예: \\[채용\\])" : "용어 (예: 휴머노이드)"}
          className={`${inputClass} w-56 ${isRegex ? "font-mono" : ""}`}
        />
        <label className="flex items-center gap-1 text-sm">
          <input
            type="checkbox"
            name="is_regex"
            checked={isRegex}
            onChange={(e) => setIsRegex(e.target.checked)}
          />
          정규식
        </label>
        <input
          type="text"
          name="note"
          maxLength={300}
          placeholder="메모 — 왜 넣었는지 (선택)"
          className={`${inputClass} w-64`}
        />
        <button type="submit" disabled={pending} className={primaryButtonClass}>
          {pending ? "추가 중…" : "추가"}
        </button>
      </div>
      <StateMessage state={state} />
    </form>
  );
}

/**
 * 규칙 칩 — 삭제만 있다 (사용자 요구 3-1).
 *
 * `enabled=false`는 화면에서 만들 수 없는 상태다(끄기 버튼을 없앴다). 그래도
 * DB에 옛날에 꺼 둔 행이 남아 있을 수 있어, 그런 행은 흐리게 + '안 쓰임'으로
 * 표시한다 — 배치가 그 규칙을 읽지 않는다는 사실을 화면이 숨기지 않게 한다.
 * 다시 켤 방법은 없으므로 지우면 된다.
 */
export function RuleChip({
  id,
  term,
  isRegex,
  enabled,
  note,
}: {
  id: string;
  term: string;
  isRegex: boolean;
  enabled: boolean;
  note: string | null;
}) {
  return (
    <div
      title={note ?? undefined}
      className={`inline-flex items-center gap-1 rounded-full border py-0.5 pl-2.5 pr-1 text-xs ${
        enabled
          ? "border-black/20 dark:border-white/25"
          : "border-dashed border-black/15 text-black/35 dark:border-white/15 dark:text-white/35"
      }`}
    >
      {isRegex && (
        <span className="font-mono text-[10px] opacity-60" title="정규식">
          re
        </span>
      )}
      <span className={`${isRegex ? "font-mono" : ""} ${enabled ? "" : "line-through"}`}>
        {term}
      </span>
      {!enabled && (
        <span
          className="text-[10px] opacity-70"
          title="예전에 꺼 둔 규칙입니다. 배치는 이 규칙을 쓰지 않습니다 — 다시 켤 수는 없으니 지우세요."
        >
          안 쓰임
        </span>
      )}
      {note && <span className="opacity-50" title={note}>ⓘ</span>}
      <form
        action={deleteKeywordRule}
        className="inline"
        onSubmit={(e) => {
          if (!window.confirm(`'${term}' 규칙을 삭제할까요? 되돌릴 수 없습니다.`)) {
            e.preventDefault();
          }
        }}
      >
        <input type="hidden" name="id" value={id} />
        <button
          type="submit"
          className={`${smallButtonClass} text-red-600`}
          title="삭제 (되돌릴 수 없음)"
          aria-label={`${term} 삭제`}
        >
          ×
        </button>
      </form>
    </div>
  );
}

/** 뉴스 필터 본문 최소 길이 설정. */
export function MinBodyLengthForm({ value }: { value: number }) {
  const [state, formAction, pending] = useActionState(updateMinBodyLength, null);
  return (
    <form action={formAction} className="space-y-1">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <label htmlFor="min-body-length">본문 최소 길이</label>
        <input
          id="min-body-length"
          type="number"
          name="value"
          min={0}
          max={999999}
          step={1}
          defaultValue={value}
          className={`${inputClass} w-24`}
        />
        <span>자</span>
        <button type="submit" disabled={pending} className={primaryButtonClass}>
          {pending ? "저장 중…" : "저장"}
        </button>
        <span className="text-xs text-black/50 dark:text-white/50">
          본문이 이보다 짧으면 보류로 두고 AI가 판단합니다.
        </span>
      </div>
      <StateMessage state={state} />
    </form>
  );
}

const VERDICT_LABEL: Record<FilterVerdict, { text: string; className: string }> = {
  PASS: {
    text: "통과 (PASS) — AI 분석 대상",
    className: "border-green-300 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-950/40 dark:text-green-200",
  },
  LOW_PRIORITY: {
    text: "보류 (LOW_PRIORITY) — 낮은 우선순위로 AI가 최종 판단",
    className: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200",
  },
  EXCLUDE: {
    text: "제외 (EXCLUDE) — AI에 보내지 않음",
    className: "border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200",
  },
};

/** 뉴스 필터 판정 미리보기. */
export function FilterTester() {
  const [state, formAction, pending] = useActionState<FilterTestState, FormData>(
    testNewsFilter,
    null,
  );
  return (
    <form action={formAction} className="space-y-2">
      <input
        type="text"
        name="title"
        maxLength={500}
        placeholder="기사 제목"
        className={`${inputClass} w-full max-w-2xl`}
      />
      <textarea
        name="body"
        rows={5}
        maxLength={20000}
        placeholder="기사 본문 (비워도 됩니다 — 본문 길이 부족으로 보류 판정이 납니다)"
        className={`${inputClass} block w-full max-w-2xl`}
      />
      <button type="submit" disabled={pending} className={primaryButtonClass}>
        {pending ? "판정 중…" : "판정해 보기"}
      </button>
      {state && !state.ok && <p className="text-sm text-red-600">{state.message}</p>}
      {state && state.ok && (
        <div className="max-w-2xl space-y-2 text-sm">
          <div className={`rounded-lg border p-3 ${VERDICT_LABEL[state.status].className}`}>
            <div className="font-semibold">{VERDICT_LABEL[state.status].text}</div>
            <div className="text-xs">{state.reason}</div>
          </div>
          <ol className="list-none space-y-0.5 text-xs text-black/70 dark:text-white/70">
            {state.steps.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ol>
          <p className="text-xs text-black/50 dark:text-white/50">{state.summary}</p>
        </div>
      )}
    </form>
  );
}
