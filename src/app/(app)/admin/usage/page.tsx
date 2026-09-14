import { requireOperator } from "@/lib/auth";

import { BatchRunsTable } from "./batch-runs";
import { GeminiDailyTable } from "./gemini-daily";
import { workflowLabel } from "./labels";
import { kstHour, quotaDatePt } from "./quota-math";
import { AnalyzeRunSection } from "./run-analyze";
import {
  callTotal,
  dayCounts,
  getBatchRuns,
  getBatchSettings,
  getGeminiHistory,
  getPendingCount,
  RUN_PAGE_SIZE,
} from "./usage-data";

export const dynamic = "force-dynamic";

const GEMINI_DAY_OPTIONS = [7, 14, 30];
const RUN_DAY_OPTIONS = [7, 30];
const DEFAULT_GEMINI_DAYS = 14;
const DEFAULT_RUN_DAYS = 7;
const MAX_RUN_PAGE = 200;

const tabClass = (active: boolean) =>
  `rounded border px-2 py-1 text-sm ${
    active
      ? "border-black/70 font-semibold dark:border-white/70"
      : "border-black/15 hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
  }`;

type View = { days: number; wf: string; wfdays: number; page: number };

function usageHref(v: View, hash = ""): string {
  const sp = new URLSearchParams();
  if (v.days !== DEFAULT_GEMINI_DAYS) sp.set("days", String(v.days));
  if (v.wf !== "all") sp.set("wf", v.wf);
  if (v.wfdays !== DEFAULT_RUN_DAYS) sp.set("wfdays", String(v.wfdays));
  if (v.page > 1) sp.set("page", String(v.page));
  const q = sp.toString();
  return `/admin/usage${q ? `?${q}` : ""}${hash}`;
}

function pickOption(
  raw: string | undefined,
  options: number[],
  fallback: number,
): number {
  const n = Number(raw);
  return options.includes(n) ? n : fallback;
}

/** Gemini·Actions 사용 현황 (FR-015, tasks §15). */
export default async function UsagePage({
  searchParams,
}: {
  searchParams: Promise<{
    days?: string;
    wf?: string;
    wfdays?: string;
    page?: string;
  }>;
}) {
  // 레이아웃도 검사하지만 이 화면은 service role로 전체 지표를 읽으므로
  // 페이지에서도 한 번 더 막는다 (다른 관리 화면과 같은 방식).
  await requireOperator();
  const params = await searchParams;

  const view: View = {
    days: pickOption(params.days, GEMINI_DAY_OPTIONS, DEFAULT_GEMINI_DAYS),
    wf: /^[a-z_-]{1,40}$/.test(params.wf ?? "") ? params.wf! : "all",
    wfdays: pickOption(params.wfdays, RUN_DAY_OPTIONS, DEFAULT_RUN_DAYS),
    page: Math.min(MAX_RUN_PAGE, Math.max(1, Number(params.page ?? "1") || 1)),
  };

  const settings = await getBatchSettings();
  const [pendingCount, history, runsResult] = await Promise.all([
    getPendingCount(),
    getGeminiHistory(view.days, settings.articleModels),
    getBatchRuns({ days: view.wfdays, workflow: view.wf, page: view.page }),
  ]);

  // 강제 분석 화면이 쓰는 값은 모두 서버에서 한 번에 정한다 — 클라이언트
  // 시계로 쿼터일·시각을 다시 구하면 화면과 서버가 다른 날짜를 볼 수 있다.
  const now = new Date();
  const quotaDate = quotaDatePt(now);
  const todayRow = history.days.find((d) => d.date === quotaDate);
  const usedByModel = settings.articleModels.map((model) => ({
    model,
    used: callTotal(dayCounts(todayRow, model)),
  }));
  const { runs, total: runTotal, names } = runsResult;
  const runPages = Math.max(1, Math.ceil(runTotal / RUN_PAGE_SIZE));
  const firstRow = (view.page - 1) * RUN_PAGE_SIZE + 1;
  const lastRow = Math.min(view.page * RUN_PAGE_SIZE, runTotal);

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold">Gemini·Actions 사용 현황</h1>

      <AnalyzeRunSection
        pendingCount={pendingCount}
        quotaDate={quotaDate}
        hour={kstHour(now)}
        models={usedByModel}
        settings={settings}
      />

      {/* ---------------- Gemini 일별 사용량 ---------------- */}
      <section id="gemini">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-semibold">Gemini 일별 사용량</h2>
          <div className="flex gap-1">
            {GEMINI_DAY_OPTIONS.map((d) => (
              <a
                key={d}
                href={usageHref({ ...view, days: d }, "#gemini")}
                className={tabClass(d === view.days)}
              >
                최근 {d}일
              </a>
            ))}
          </div>
        </div>
        <GeminiDailyTable
          history={history}
          articleModels={settings.articleModels}
          softLimit={settings.dailySoftLimit}
          todayQuotaDate={quotaDate}
        />
        {history.truncated && (
          <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
            기록이 많아 조회 상한(2만 건)에 걸렸습니다 — 최근{" "}
            {history.days.length}일치만 집계한 표입니다.
          </p>
        )}
      </section>

      {/* ---------------- 배치 실행 이력 ---------------- */}
      <section id="runs">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-semibold">배치 실행 이력</h2>
          <div className="flex gap-1">
            {RUN_DAY_OPTIONS.map((d) => (
              <a
                key={d}
                href={usageHref({ ...view, wfdays: d, page: 1 }, "#runs")}
                className={tabClass(d === view.wfdays)}
              >
                최근 {d}일
              </a>
            ))}
          </div>
        </div>

        <div className="mt-2 flex flex-wrap gap-1">
          <a
            href={usageHref({ ...view, wf: "all", page: 1 }, "#runs")}
            className={tabClass(view.wf === "all")}
          >
            전체
          </a>
          {names.map((n) => (
            <a
              key={n}
              href={usageHref({ ...view, wf: n, page: 1 }, "#runs")}
              className={tabClass(view.wf === n)}
            >
              {workflowLabel(n)}
            </a>
          ))}
        </div>

        <p className="mt-2 text-sm text-black/55 dark:text-white/55">
          최근 {view.wfdays}일 ·{" "}
          {view.wf === "all" ? "전체 워크플로" : workflowLabel(view.wf)} ·{" "}
          {runTotal === 0
            ? "0건"
            : `${runTotal.toLocaleString("ko-KR")}건 중 ${firstRow}–${lastRow}`}
        </p>

        <BatchRunsTable runs={runs} />

        {runPages > 1 && (
          <div className="mt-3 flex items-center gap-3 text-sm">
            {view.page > 1 && (
              <a
                href={usageHref({ ...view, page: view.page - 1 }, "#runs")}
                className="underline-offset-2 hover:underline"
              >
                ← 이전
              </a>
            )}
            <span className="text-black/50 dark:text-white/50">
              {view.page} / {runPages}
            </span>
            {view.page < runPages && (
              <a
                href={usageHref({ ...view, page: view.page + 1 }, "#runs")}
                className="underline-offset-2 hover:underline"
              >
                다음 →
              </a>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
