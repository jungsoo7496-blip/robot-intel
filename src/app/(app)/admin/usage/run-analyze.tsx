"use client";

import { useActionState, useState } from "react";

import { triggerAnalyzeNow } from "../actions";
import { modelLabel } from "./labels";
import {
  ANALYZE_BOTH,
  articleMinutes,
  DRAIN_BUDGET_SECONDS,
  MANUAL_COUNT_MAX,
  MANUAL_COUNT_MIN,
  MANUAL_RUN_DAILY_LIMIT,
  planAnalyzeRun,
  quotaAllowance,
  type QuotaSettings,
} from "./quota-math";

const selectClass =
  "mt-1 rounded-lg border border-black/20 bg-transparent px-3 py-2 text-sm dark:border-white/25";

/** 드롭다운에 띄우는 건수 후보 (실제 목록은 남은 여유로 걸러진다). */
const COUNT_CANDIDATES = [50, 100, 150, 200, 280, 400];

export type AnalyzeRunProps = {
  /** 분석 대기(PENDING·RETRY) 건수 — 배치가 이보다 많이 처리할 수는 없다. */
  pendingCount: number;
  /** 쿼터일(UTC-8)과 한국 시각의 시 — 서버에서 정해 넘긴다(클라이언트 시계와 어긋나지 않게). */
  quotaDate: string;
  hour: number;
  /** 분석에 쓰는 모델과 오늘 쓴 호출 수. */
  models: { model: string; used: number }[];
  settings: QuotaSettings;
};

/**
 * 지금 강제 분석 — 모델과 건수를 골라 analyze 워크플로를 즉시 돌린다.
 *
 * 클라이언트 컴포넌트인 이유: 남은 여유도 처리 속도도 **고른 모델에 따라
 * 달라진다**. 서버에서 한 번 계산해 두면 드롭다운을 바꿔도 숫자가 그대로라,
 * 화면은 고를 수 있다고 하는데 서버 액션은 거절하는 상태가 된다.
 * 계산식은 quota-math.ts 하나뿐이고 서버 액션도 같은 함수를 쓴다.
 */
export function AnalyzeRunSection({
  pendingCount,
  quotaDate,
  hour,
  models,
  settings,
}: AnalyzeRunProps) {
  const [state, formAction, pending] = useActionState(triggerAnalyzeNow, null);
  const [model, setModel] = useState<string>(ANALYZE_BOTH);
  const [count, setCount] = useState<string>("");

  const allUsed = models.map((m) => m.used);
  const all = quotaAllowance(allUsed, settings, hour);

  // 이번에 쓰는 모델 = '2개 모델 동시'면 전부, 아니면 고른 하나
  const usedByModel =
    model === ANALYZE_BOTH
      ? allUsed
      : [models.find((m) => m.model === model)?.used ?? 0];
  const picked = quotaAllowance(usedByModel, settings, hour);

  // 서버 액션(admin/actions.ts)이 거절하는 값은 아예 고르지 못하게 한다 —
  // 두 쪽 모두 quota-math의 같은 articlesLeft로 판정한다.
  const countOptions = [...new Set([...COUNT_CANDIDATES, settings.batchMaxCount])]
    .sort((a, b) => a - b)
    .filter(
      (n) =>
        n >= MANUAL_COUNT_MIN &&
        n <= MANUAL_COUNT_MAX &&
        n <= picked.articlesLeft,
    );
  if (countOptions.length === 0 && picked.articlesLeft >= MANUAL_COUNT_MIN) {
    countOptions.push(picked.articlesLeft);
  }

  // 모델을 바꿔 고른 건수가 한도를 넘게 되면 '시간 되는 만큼'으로 되돌린다
  const chosen = count === "" ? null : Number(count);
  const requested = chosen !== null && countOptions.includes(chosen) ? chosen : null;
  const countValue = requested === null ? "" : String(requested);

  const plan = (requestedCount: number | null) =>
    planAnalyzeRun({
      hour,
      usedByModel,
      settings,
      pendingCount,
      requestedCount,
    });
  const defaultPlan = plan(null);
  const runPlan = requested === null ? defaultPlan : plan(requested);
  const modelCount = usedByModel.length;

  // 막차 소진은 '리셋 3시간 전 + 대기가 이번 배치 처리량 이상'일 때 걸린다.
  // 건수를 지정해도 걸린다 — 지정한 캡은 그대로 두고 시간 예산만 늘어난다.
  // 조건이 안 맞으면 안내하지 않는다: 안 걸리는데 "540초로 늘어난다"고 쓰면
  // 그게 또 다른 거짓말이 된다.
  const drainOffered = runPlan.drain;
  /** 이번에 쓸 수 있는 모델이 하나도 안 남았나. */
  const anyLeft = all.articlesLeft > 0;
  const canRun = picked.articlesLeft > 0;

  const limitReason: Record<string, string> = {
    time: `실행 시간 예산 ${runPlan.budgetSeconds}초가 먼저 찹니다`,
    count: `고른 건수 ${runPlan.countCap.toLocaleString("ko-KR")}건에 먼저 닿습니다`,
    quota: "오늘 남은 쿼터가 먼저 바닥납니다",
    queue: "분석 대기를 다 처리하고 끝납니다",
  };

  return (
    <section className="rounded-xl border border-black/10 bg-white p-4 dark:border-white/12 dark:bg-white/[0.03]">
      <h2 className="font-semibold">
        지금 강제 분석 — 분석 대기 {pendingCount.toLocaleString("ko-KR")}건
      </h2>
      <p className="mt-1 text-sm text-black/60 dark:text-white/60">
        누르면 30초~2분 안에 시작합니다. <strong>2개 모델 동시</strong>가 기본
        — 같은 실행 시간에 두 배를 처리합니다.
      </p>

      <table className="mt-3 text-sm">
        <thead className="text-left text-black/55 dark:text-white/55">
          <tr>
            <th className="pr-6 font-medium">모델</th>
            <th className="pr-6 font-medium">오늘 쓴 호출</th>
            <th className="font-medium">남은 호출</th>
          </tr>
        </thead>
        <tbody>
          {models.map((m) => (
            <tr key={m.model}>
              <td className="pr-6">{modelLabel(m.model)}</td>
              <td className="pr-6 tabular-nums">
                {m.used.toLocaleString("ko-KR")} /{" "}
                {all.perModelCap.toLocaleString("ko-KR")}
              </td>
              <td className="tabular-nums">
                {Math.max(0, all.perModelCap - m.used).toLocaleString("ko-KR")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-xs text-black/50 dark:text-white/50">
        쿼터일 {quotaDate} 기준 — 매일 한국 시각 17시에 새로 시작합니다. 모델당
        오늘 쓸 수 있는 {all.perModelCap.toLocaleString("ko-KR")}회는 하루 목표{" "}
        {settings.dailySoftLimit.toLocaleString("ko-KR")}회에서 브리프 예약{" "}
        {settings.briefDailyReserve}회
        {all.night &&
          ` · 야간이라 조간 분석 몫 ${settings.morningReserveCalls}회`}
        를 뺀 값입니다.
      </p>

      {anyLeft ? (
        <>
          <form action={formAction} className="mt-4 flex flex-wrap items-end gap-3">
            <label className="text-sm">
              <span className="block text-black/60 dark:text-white/60">
                이번에 몇 건까지
              </span>
              <select
                name="max_count"
                value={countValue}
                onChange={(e) => setCount(e.target.value)}
                className={selectClass}
              >
                <option value="">
                  {defaultPlan.expected > 0
                    ? `시간 되는 만큼 (기본) · 약 ${defaultPlan.expected}건 · ${articleMinutes(
                        defaultPlan.expected,
                        modelCount,
                      )}분`
                    : "시간 되는 만큼 (기본)"}
                </option>
                {countOptions.map((n) => {
                  const p = plan(n);
                  const mins = articleMinutes(p.expected, modelCount);
                  return (
                    <option key={n} value={n}>
                      {p.expected < n
                        ? `${n}건 지정 · 이번엔 약 ${p.expected}건 · ${mins}분`
                        : `${n}건 · 약 ${mins}분`}
                    </option>
                  );
                })}
              </select>
            </label>
            <label className="text-sm">
              <span className="block text-black/60 dark:text-white/60">모델</span>
              <select
                name="model"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                className={selectClass}
              >
                <option value={ANALYZE_BOTH}>2개 모델 동시 (권장)</option>
                {models.map((m) => (
                  <option key={m.model} value={m.model}>
                    {modelLabel(m.model)} 단독
                  </option>
                ))}
              </select>
            </label>
            <button
              type="submit"
              disabled={pending || !canRun}
              className="rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background hover:opacity-90 disabled:opacity-50"
            >
              {pending ? "요청 중…" : "지금 실행"}
            </button>
          </form>

          {state && (
            <p
              aria-live="polite"
              className={`mt-3 rounded-lg px-3 py-2 text-sm ${
                state.ok
                  ? "bg-green-50 text-green-800 dark:bg-green-950/40 dark:text-green-300"
                  : "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
              }`}
            >
              {state.message}
            </p>
          )}

          {canRun ? (
            <p className="mt-3 text-sm text-black/60 dark:text-white/60">
              지금 시작하면{" "}
              <strong>
                약 {runPlan.expected.toLocaleString("ko-KR")}건에서 멈춥니다
              </strong>{" "}
              (약 {articleMinutes(runPlan.expected, modelCount)}분) —{" "}
              {limitReason[runPlan.limitedBy]}. 남은 쿼터로는 약{" "}
              {picked.articlesLeft.toLocaleString("ko-KR")}건, 시간 예산{" "}
              {runPlan.budgetSeconds}초로는 약{" "}
              {runPlan.budgetArticles.toLocaleString("ko-KR")}건입니다. 처리하지
              못한 만큼은 다음 실행으로 넘어갑니다.
            </p>
          ) : (
            <p className="mt-3 text-sm text-amber-700 dark:text-amber-400">
              {modelLabel(model)}는 오늘 남은 호출이 없어 지금 실행해도
              처리되지 않습니다. 위 표에서 여유가 남은 모델이나 &lsquo;2개 모델
              동시&rsquo;를 고르세요.
            </p>
          )}

          {canRun && drainOffered && (
            <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
              지금은 쿼터 리셋(17시) 전 <strong>막차 시간</strong>이라 배치가
              시간 예산을 {DRAIN_BUDGET_SECONDS}초로 늘립니다. 건수를 지정해도
              이 증량은 그대로 켜집니다 — 고른 숫자는 상한으로만 쓰입니다.
              {requested !== null && runPlan.expected < defaultPlan.expected
                ? ` 비워 두면 약 ${defaultPlan.expected.toLocaleString("ko-KR")}건까지 가는데, 고른 ${requested.toLocaleString("ko-KR")}건이 그보다 낮아 약 ${runPlan.expected.toLocaleString("ko-KR")}건에서 멈춥니다.`
                : ` 이번 실행은 약 ${runPlan.expected.toLocaleString("ko-KR")}건까지 갑니다.`}
            </p>
          )}

          <p className="mt-1 text-sm text-black/60 dark:text-white/60">
            24시간에 {MANUAL_RUN_DAILY_LIMIT}회까지. 앞 실행이 끝나기 전에
            누르면 거부됩니다 — 아래 표에서 &lsquo;실행 중&rsquo;인지
            확인하세요.
          </p>
        </>
      ) : (
        <p className="mt-4 text-sm text-amber-700 dark:text-amber-400">
          오늘 남은 쿼터가 없어 지금은 실행해도 처리되지 않습니다.
          {all.night
            ? ` 한국 시각 6시가 지나면 조간 몫 ${settings.morningReserveCalls}회가 풀립니다.`
            : " 한국 시각 17시에 쿼터가 새로 시작합니다."}
        </p>
      )}
    </section>
  );
}
