"use client";

import { useActionState } from "react";

import { runCleanup, updateRetentionSettings } from "./actions";

export type RetentionField = {
  key: string;
  label: string;
  help: string;
  kind: "days" | "bool";
};

function ResultMessage({
  state,
}: {
  state: { ok: boolean; message: string } | null;
}) {
  if (!state) return null;
  return (
    <p
      aria-live="polite"
      className={`text-sm ${state.ok ? "text-green-700 dark:text-green-400" : "text-red-600"}`}
    >
      {state.message}
    </p>
  );
}

/** 보존 정책 편집 폼 — app_settings 6개 (숫자·불리언 그대로 저장). */
export function RetentionForm({
  fields,
  values,
}: {
  fields: RetentionField[];
  values: Record<string, number | boolean>;
}) {
  const [state, formAction, pending] = useActionState(updateRetentionSettings, null);

  return (
    <form action={formAction} className="space-y-3">
      <div className="grid gap-3 md:grid-cols-2">
        {fields.map((f) => (
          <label
            key={f.key}
            className="flex flex-col gap-1 rounded-lg border border-black/10 p-3 text-sm dark:border-white/15"
          >
            <span className="font-medium">{f.label}</span>
            {f.kind === "days" ? (
              <span className="flex items-center gap-2">
                <input
                  type="number"
                  name={f.key}
                  min={0}
                  max={365}
                  step={1}
                  required
                  defaultValue={Number(values[f.key] ?? 0)}
                  className="w-24 rounded border border-black/20 bg-transparent px-2 py-1 dark:border-white/25"
                />
                <span className="text-black/60 dark:text-white/60">일 (0 = 안 함)</span>
              </span>
            ) : (
              <span className="flex items-center gap-2">
                <input
                  type="checkbox"
                  name={f.key}
                  defaultChecked={values[f.key] === true}
                  className="h-4 w-4"
                />
                <span className="text-black/60 dark:text-white/60">보존한다</span>
              </span>
            )}
            <span className="text-xs text-black/50 dark:text-white/50">{f.help}</span>
          </label>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={pending}
          className="rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background hover:opacity-90 disabled:opacity-50"
        >
          {pending ? "저장 중…" : "보존 정책 저장"}
        </button>
        <ResultMessage state={state} />
      </div>
    </form>
  );
}

export type CleanupPreset = {
  preset: "daily" | "purge_columns" | "purge_backlog";
  title: string;
  description: string;
  estimate: string;
  primary?: boolean;
};

function CleanupButton({ item }: { item: CleanupPreset }) {
  const [state, formAction, pending] = useActionState(runCleanup, null);
  const checkboxId = `confirm-${item.preset}`;

  return (
    <form
      action={formAction}
      className="flex flex-col gap-2 rounded-lg border border-black/10 p-4 text-sm dark:border-white/15"
    >
      <input type="hidden" name="preset" value={item.preset} />
      <div className="font-semibold">{item.title}</div>
      <p className="text-black/60 dark:text-white/60">{item.description}</p>
      <p className="text-xs text-black/60 dark:text-white/60">
        <span className="font-medium">예상 회수:</span> {item.estimate}
      </p>
      <p className="text-xs text-red-700 dark:text-red-300">
        되돌릴 수 없습니다 — 비운 본문·응답 원문은 다시 만들 수 없습니다.
      </p>
      <label htmlFor={checkboxId} className="flex items-center gap-2">
        <input id={checkboxId} type="checkbox" name="confirm" className="h-4 w-4" />
        <span>내용을 확인했고 실행에 동의합니다</span>
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={pending}
          className={
            item.primary
              ? "rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background hover:opacity-90 disabled:opacity-50"
              : "rounded-lg border border-black/20 px-4 py-2 text-sm font-semibold hover:border-black/40 disabled:opacity-50 dark:border-white/25 dark:hover:border-white/45"
          }
        >
          {pending ? "요청 중…" : item.title}
        </button>
      </div>
      <ResultMessage state={state} />
    </form>
  );
}

/** 정리 실행 버튼 3종 — 각각 확인 체크박스가 켜져야 실행된다. */
export function CleanupActions({ items }: { items: CleanupPreset[] }) {
  return (
    <div className="grid gap-3 lg:grid-cols-3">
      {items.map((item) => (
        <CleanupButton key={item.preset} item={item} />
      ))}
    </div>
  );
}
