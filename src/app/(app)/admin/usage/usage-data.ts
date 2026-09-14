import "server-only";

import { createServiceRoleClient } from "@/lib/supabase/server";

import { DAY_MS, quotaDatePt, settingNumber } from "./quota-math";

/** days일 전의 쿼터일 (오늘 포함 범위의 시작점을 구할 때 쓴다). */
function quotaDateBefore(days: number, now: Date = new Date()): string {
  return quotaDatePt(new Date(now.getTime() - days * DAY_MS));
}

// ------------------------------------------------------------
// 배치 설정 — app_settings 값을 그대로 읽는다. 기본값은 배치의
// scripts/kiro_batch/config.py 와 같아야 한다.
// ------------------------------------------------------------
const SETTING_DEFAULTS = {
  article_models: "gemini-flash-lite-latest,gemini-3.1-flash-lite",
  article_daily_soft_limit: 480,
  brief_daily_reserve: 5,
  morning_reserve_calls: 150,
  article_batch_max_count: 400,
  article_batch_max_seconds: 420,
  article_target_rpm: 12,
  article_stream_workers: 2,
};

export type BatchSettings = {
  articleModels: string[];
  dailySoftLimit: number;
  briefDailyReserve: number;
  morningReserveCalls: number;
  batchMaxCount: number;
  batchMaxSeconds: number;
  targetRpm: number;
  streamWorkers: number;
};

export async function getBatchSettings(): Promise<BatchSettings> {
  const supabase = createServiceRoleClient();
  const { data } = await supabase
    .from("app_settings")
    .select("key, value")
    .in("key", Object.keys(SETTING_DEFAULTS));

  const raw = new Map<string, unknown>(
    (data ?? []).map((r) => [r.key as string, r.value]),
  );
  // 숫자 읽는 규칙은 quota-math.settingNumber 하나뿐이다 — 서버 액션
  // (admin/actions.ts)도 같은 함수를 쓴다.
  const num = (key: keyof typeof SETTING_DEFAULTS) =>
    settingNumber(raw.get(key), SETTING_DEFAULTS[key] as number);

  const models = String(
    raw.get("article_models") ?? SETTING_DEFAULTS.article_models,
  )
    .split(",")
    .map((m) => m.trim())
    .filter(Boolean);

  return {
    articleModels: models,
    dailySoftLimit: num("article_daily_soft_limit"),
    briefDailyReserve: num("brief_daily_reserve"),
    morningReserveCalls: num("morning_reserve_calls"),
    batchMaxCount: num("article_batch_max_count"),
    batchMaxSeconds: num("article_batch_max_seconds"),
    targetRpm: num("article_target_rpm"),
    streamWorkers: num("article_stream_workers"),
  };
}

// ------------------------------------------------------------
// Gemini 일별 사용량
// ------------------------------------------------------------
export type CallCounts = { ok: number; limited: number; error: number };
export type GeminiDay = {
  date: string;
  models: Map<string, CallCounts>;
  total: number;
};
export type GeminiHistory = {
  days: GeminiDay[];
  models: string[];
  /** 조회 상한에 걸려 오래된 날짜를 다 못 읽었다 */
  truncated: boolean;
};

// PostgREST가 한 번에 돌려주는 최대 행 수(1000)에 맞춘 페이지 크기.
// gemini_calls는 하루 약 500행이라 30일이면 1만 5천 행 정도 된다 —
// 집계 함수를 쓸 수 없으므로(REST에서 막혀 있음) 나눠 읽어 합산한다.
const CALL_PAGE_SIZE = 1000;
const CALL_MAX_PAGES = 20; // 최대 2만 행까지만 읽는다

export async function getGeminiHistory(
  days: number,
  articleModels: string[],
): Promise<GeminiHistory> {
  const supabase = createServiceRoleClient();
  const since = quotaDateBefore(days - 1);

  const { count } = await supabase
    .from("gemini_calls")
    .select("id", { count: "exact", head: true })
    .gte("quota_date_pt", since);

  const total = count ?? 0;
  const pageCount = Math.min(Math.ceil(total / CALL_PAGE_SIZE), CALL_MAX_PAGES);
  const truncated = total > CALL_MAX_PAGES * CALL_PAGE_SIZE;

  // 최신 날짜부터 읽는다 — 상한에 걸리면 잘려나가는 쪽이 '가장 오래된 날'이
  // 되도록. id를 두 번째 정렬 키로 둬야 페이지 경계가 흔들리지 않는다.
  const pages = await Promise.all(
    Array.from({ length: pageCount }, (_, i) =>
      supabase
        .from("gemini_calls")
        .select("quota_date_pt, model_name, status")
        .gte("quota_date_pt", since)
        .order("quota_date_pt", { ascending: false })
        .order("id", { ascending: true })
        .range(i * CALL_PAGE_SIZE, (i + 1) * CALL_PAGE_SIZE - 1),
    ),
  );

  const byDay = new Map<string, Map<string, CallCounts>>();
  const modelSeen = new Set<string>();

  for (const page of pages) {
    for (const row of page.data ?? []) {
      const date = String(row.quota_date_pt);
      const model = String(row.model_name);
      modelSeen.add(model);
      let dayMap = byDay.get(date);
      if (!dayMap) {
        dayMap = new Map();
        byDay.set(date, dayMap);
      }
      let c = dayMap.get(model);
      if (!c) {
        c = { ok: 0, limited: 0, error: 0 };
        dayMap.set(model, c);
      }
      if (row.status === "OK") c.ok += 1;
      else if (row.status === "RATE_LIMITED") c.limited += 1;
      else c.error += 1;
    }
  }

  // 호출이 하나도 없던 날도 빈 줄로 남긴다 — 배치가 통째로 걸렀는지
  // 한눈에 보여야 하므로 그 날짜가 표에서 사라지면 안 된다.
  let cutoff = since;
  if (truncated) {
    // 상한에 걸렸으면 가장 오래된 날은 중간에서 잘린 반쪽이므로 버린다.
    const present = [...byDay.keys()].sort();
    cutoff = present[1] ?? present[0] ?? since;
  }
  const days_: GeminiDay[] = [];
  for (let i = 0; i < days; i++) {
    const date = quotaDatePt(new Date(Date.now() - i * DAY_MS));
    if (date < cutoff) break;
    const models = byDay.get(date) ?? new Map<string, CallCounts>();
    let sum = 0;
    for (const c of models.values()) sum += c.ok + c.limited + c.error;
    days_.push({ date, models, total: sum });
  }

  // 분석 모델을 설정 순서대로 앞에 두고, 나머지(브리프 등)는 이름순으로 뒤에.
  // 분석 모델은 호출이 0이어도 칸을 남긴다 — 한쪽 모델이 통째로 안 돌고
  // 있으면 그게 보여야 한다.
  const others = [...modelSeen]
    .filter((m) => !articleModels.includes(m))
    .sort();
  const models = [...articleModels, ...others];

  return { days: days_, models, truncated };
}

export function dayCounts(day: GeminiDay | undefined, model: string): CallCounts {
  return day?.models.get(model) ?? { ok: 0, limited: 0, error: 0 };
}

export function callTotal(c: CallCounts): number {
  return c.ok + c.limited + c.error;
}

// ------------------------------------------------------------
// 배치 실행 이력 (workflow_usage)
// ------------------------------------------------------------
export type BatchRun = {
  id: string;
  workflow_name: string;
  note: string | null;
  started_at: string;
  duration_seconds: number | null;
  collected_count: number;
  analyzed_count: number;
  api_call_count: number;
  remaining_pending_count: number | null;
  status: string;
};

export const RUN_PAGE_SIZE = 25;

export async function getBatchRuns(opts: {
  days: number;
  workflow: string;
  page: number;
}): Promise<{ runs: BatchRun[]; total: number; names: string[] }> {
  const supabase = createServiceRoleClient();
  const since = new Date(Date.now() - opts.days * DAY_MS).toISOString();

  let listQuery = supabase
    .from("workflow_usage")
    .select(
      `id, workflow_name, note, started_at, duration_seconds, collected_count,
       analyzed_count, api_call_count, remaining_pending_count, status`,
      { count: "exact" },
    )
    .gte("started_at", since);
  if (opts.workflow !== "all") {
    listQuery = listQuery.eq("workflow_name", opts.workflow);
  }

  // 종류 필터 목록은 실제로 기록이 있는 이름에서 뽑는다 (새 워크플로가
  // 생겨도 화면이 따라간다). 이 표는 30일이 300건 남짓이라 가볍다.
  const [listRes, nameRes] = await Promise.all([
    listQuery
      .order("started_at", { ascending: false })
      .range(
        (opts.page - 1) * RUN_PAGE_SIZE,
        opts.page * RUN_PAGE_SIZE - 1,
      ),
    supabase
      .from("workflow_usage")
      .select("workflow_name")
      .gte("started_at", since)
      .limit(1000),
  ]);

  const names = [
    ...new Set((nameRes.data ?? []).map((r) => String(r.workflow_name))),
  ].sort();

  return {
    runs: (listRes.data ?? []) as BatchRun[],
    total: listRes.count ?? 0,
    names,
  };
}

/** 분석 대기(PENDING·RETRY) 건수. */
export async function getPendingCount(): Promise<number> {
  const supabase = createServiceRoleClient();
  const { count } = await supabase
    .from("analysis_jobs")
    .select("id", { count: "exact", head: true })
    .in("status", ["PENDING", "RETRY"]);
  return count ?? 0;
}
