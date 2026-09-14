"use client";

import { useActionState, useId, useState } from "react";

import {
  createReportSource,
  deleteReportSource,
  retireReportSource,
  triggerCollectReportsNow,
  updateReportSource,
  type ActionState,
  type ReportSourceFormOptions,
  type ReportSourceRow,
} from "./actions";

export const INPUT_CLASS =
  "w-full rounded border border-black/20 bg-transparent px-3 py-2 text-sm dark:border-white/25";
export const BUTTON_PRIMARY_CLASS =
  "rounded bg-foreground px-4 py-2 text-sm font-medium text-background hover:opacity-90 disabled:opacity-50";
export const BUTTON_SMALL_CLASS =
  "rounded border border-black/20 px-2 py-1 text-xs hover:bg-black/5 disabled:opacity-50 dark:border-white/25 dark:hover:bg-white/10";

/** 서버 액션 결과 문구 ({ ok, message } 패턴). */
export function ActionMessage({ state }: { state: ActionState }) {
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

/** 라벨 + 입력 + 도움말 한 묶음. */
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block text-xs font-medium text-black/60 dark:text-white/60">
        {label}
      </span>
      {children}
      {hint && (
        <span className="mt-1 block text-xs text-black/45 dark:text-white/45">{hint}</span>
      )}
    </label>
  );
}

/**
 * 수집원 추가·편집 폼. 버튼과 (펼쳐진) 폼을 fragment로 돌려주므로, 부모의
 * flex-wrap 행에서 버튼은 한 줄에, 폼은 basis-full로 다음 줄 전체를 차지한다.
 */
export function SourceForm({
  mode,
  initial,
  options,
  sourceKinds,
}: {
  mode: "create" | "edit";
  initial?: ReportSourceRow;
  options: ReportSourceFormOptions;
  sourceKinds: string[];
}) {
  const isEdit = mode === "edit";
  const [open, setOpen] = useState(false);
  const [state, formAction, pending] = useActionState(
    isEdit ? updateReportSource : createReportSource,
    null,
  );
  const kindsListId = useId();

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={isEdit ? BUTTON_SMALL_CLASS : BUTTON_PRIMARY_CLASS}
      >
        {open ? (isEdit ? "편집 닫기" : "추가 취소") : isEdit ? "편집" : "수집원 추가"}
      </button>

      {open && (
        <form
          action={formAction}
          className="basis-full space-y-3 rounded-lg border border-black/10 p-4 dark:border-white/15"
        >
          {isEdit && initial && <input type="hidden" name="id" value={initial.id} />}

          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              label="source_key (영문 소문자 · 만든 뒤 변경 불가)"
              hint={isEdit ? "배치·통계가 이 키로 참조하므로 바꿀 수 없습니다." : "예: kdi, kiet-report — 소문자·숫자·-·_ 2~40자"}
            >
              {isEdit && initial ? (
                <input className={`${INPUT_CLASS} opacity-60`} value={initial.source_key} readOnly />
              ) : (
                <input
                  name="source_key"
                  required
                  pattern="[a-z][a-z0-9_\-]{1,39}"
                  placeholder="kdi"
                  className={INPUT_CLASS}
                />
              )}
            </Field>
            <Field label="이름">
              <input
                name="name"
                required
                maxLength={120}
                defaultValue={initial?.name ?? ""}
                placeholder="예: 한국개발연구원(KDI)"
                className={INPUT_CLASS}
              />
            </Field>
            <Field label="기본 URL (선택)" hint="사이트 첫 화면 주소 — 참고용">
              <input
                name="base_url"
                type="url"
                defaultValue={initial?.base_url ?? ""}
                placeholder="https://"
                className={INPUT_CLASS}
              />
            </Field>
            <Field
              label="종류 (선택)"
              hint={`기존 값: ${sourceKinds.length ? sourceKinds.join(", ") : "없음"} — 직접 입력해도 됩니다`}
            >
              <input
                name="source_kind"
                list={kindsListId}
                defaultValue={initial?.source_kind ?? ""}
                placeholder="PORTAL"
                className={INPUT_CLASS}
              />
              <datalist id={kindsListId}>
                {sourceKinds.map((k) => (
                  <option key={k} value={k} />
                ))}
              </datalist>
            </Field>
            <Field label="상태" hint="'정상(ACTIVE)'일 때만 배치가 이 수집원의 자료 종류를 실행합니다.">
              <select
                name="status"
                defaultValue={initial?.status ?? "CANDIDATE"}
                className={INPUT_CLASS}
              >
                {options.statuses.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label} ({s.value})
                  </option>
                ))}
              </select>
            </Field>
            <Field label="우선순위" hint="숫자가 작을수록 먼저 실행 (기본 100)">
              <input
                name="priority"
                type="number"
                min={0}
                max={10000}
                defaultValue={initial?.priority ?? 100}
                className={INPUT_CLASS}
              />
            </Field>
            <Field label="운영 기관 (선택)">
              <input
                name="owner_org"
                maxLength={120}
                defaultValue={initial?.owner_org ?? ""}
                placeholder="예: 국무조정실"
                className={INPUT_CLASS}
              />
            </Field>
            <Field
              label="어댑터 (수집 코드)"
              hint="자료 종류에서 따로 고르지 않으면 이 어댑터를 씁니다. 목록에 없는 사이트는 어댑터를 먼저 구현해야 합니다."
            >
              <select
                name="adapter_key"
                defaultValue={initial?.adapter_key ?? ""}
                className={INPUT_CLASS}
              >
                <option value="">없음 — 아직 수집 불가</option>
                {options.adapterKeys.map((k) => (
                  <option key={k} value={k}>
                    {options.adapterInfo[k]?.label ?? k}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <Field label="설명 (선택)">
            <textarea
              name="description"
              rows={2}
              maxLength={1000}
              defaultValue={initial?.description ?? ""}
              placeholder="어떤 자료를 제공하는 곳인지, API 승인 상태 등"
              className={INPUT_CLASS}
            />
          </Field>
          <Field label="운영 메모 (선택)">
            <textarea
              name="notes"
              rows={2}
              maxLength={2000}
              defaultValue={initial?.notes ?? ""}
              placeholder="예: 키 승인 대기 중, 담당자 연락처 등"
              className={INPUT_CLASS}
            />
          </Field>

          <div className="flex flex-wrap items-center gap-3">
            <button type="submit" disabled={pending} className={BUTTON_PRIMARY_CLASS}>
              {pending ? "저장 중…" : isEdit ? "저장" : "추가"}
            </button>
            <ActionMessage state={state} />
          </div>
        </form>
      )}
    </>
  );
}

/**
 * 수집원 삭제 — 모은 자료가 없으면 하드 삭제(채널도 함께), 있으면 '종료' 전환을
 * 안내한다. 최종 판정은 서버 액션이 다시 세어서 한다.
 */
export function SourceDeleteButton({
  source,
  occurrenceCount,
}: {
  source: ReportSourceRow;
  occurrenceCount: number;
}) {
  const [state, formAction, pending] = useActionState(deleteReportSource, null);

  if (occurrenceCount > 0) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-xs text-black/55 dark:text-white/55">
        <span>
          모은 자료 {occurrenceCount.toLocaleString("ko-KR")}건이 있어 삭제할 수 없습니다 —
          수집을 멈추려면 &lsquo;종료&rsquo;로 전환하세요.
        </span>
        {source.status !== "RETIRED" && (
          <form
            action={retireReportSource}
            onSubmit={(e) => {
              if (!window.confirm(`'${source.name}'을(를) 종료(RETIRED)로 바꿀까요? 배치가 더 이상 수집하지 않습니다.`)) {
                e.preventDefault();
              }
            }}
          >
            <input type="hidden" name="id" value={source.id} />
            <button type="submit" className={BUTTON_SMALL_CLASS}>
              종료(RETIRED)로 전환
            </button>
          </form>
        )}
      </div>
    );
  }

  return (
    <form
      action={formAction}
      onSubmit={(e) => {
        if (!window.confirm(`수집원 '${source.name}'과 그 자료 종류를 모두 삭제할까요? 되돌릴 수 없습니다.`)) {
          e.preventDefault();
        }
      }}
      className="flex flex-wrap items-center gap-2"
    >
      <input type="hidden" name="id" value={source.id} />
      <button
        type="submit"
        disabled={pending}
        className={`${BUTTON_SMALL_CLASS} text-red-600 dark:text-red-400`}
      >
        {pending ? "삭제 중…" : "삭제"}
      </button>
      <ActionMessage state={state} />
    </form>
  );
}

/** '보고서 수집 지금 실행' — collect-reports 워크플로 즉시 트리거. */
export function CollectReportsNowButton() {
  const [state, formAction, pending] = useActionState(triggerCollectReportsNow, null);
  return (
    <form action={formAction} className="flex flex-col items-end gap-1">
      <button
        type="submit"
        disabled={pending}
        className="rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background hover:opacity-90 disabled:opacity-50"
      >
        {pending ? "요청 중…" : "보고서 수집 지금 실행"}
      </button>
      {state && (
        <p
          aria-live="polite"
          className={`max-w-md text-right text-xs ${state.ok ? "text-green-700 dark:text-green-400" : "text-red-600"}`}
        >
          {state.message}
        </p>
      )}
    </form>
  );
}
