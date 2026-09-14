import "server-only";

import { createServiceRoleClient } from "@/lib/supabase/server";

// Gemini 쿼터일 규칙(UTC-8 고정)은 한 곳에만 둔다 — 배치 quota.py와 같은 규칙.
import { quotaDatePt } from "./usage/quota-math";

function kstTodayStartUtc(): string {
  const now = new Date();
  const kst = new Date(now.getTime() + 9 * 3600_000);
  const start = Date.UTC(
    kst.getUTCFullYear(), kst.getUTCMonth(), kst.getUTCDate(),
  ) - 9 * 3600_000;
  return new Date(start).toISOString();
}

/** 운영 현황 수치 (tasks §15.2). requireOperator 이후에만 호출한다. */
export async function getDashboardStats() {
  const supabase = createServiceRoleClient();
  const todayStart = kstTodayStartUtc();
  const monthStart = new Date();
  monthStart.setUTCDate(1);
  monthStart.setUTCHours(0, 0, 0, 0);

  const [
    collectedToday,
    publishedToday,
    excludedTotal,
    clusters,
    jobStats,
    lastCollect,
    lastAnalyze,
    geminiToday,
    pendingJobs,
    staleRecovered,
    latestBrief,
    latestBackup,
    monthUsage,
    openReports,
    failingSources,
    operatorHidden,
  ] = await Promise.all([
    supabase.from("raw_items").select("id", { count: "exact", head: true })
      .gte("fetched_at", todayStart),
    supabase.from("published_items").select("id", { count: "exact", head: true })
      .gte("published_at", todayStart),
    supabase.from("raw_items").select("id", { count: "exact", head: true })
      .eq("filter_status", "EXCLUDE"),
    supabase.from("content_clusters").select("id", { count: "exact", head: true }),
    supabase.from("analysis_jobs").select("status"),
    supabase.from("workflow_usage").select("started_at")
      .eq("workflow_name", "collect").eq("status", "SUCCESS")
      .order("started_at", { ascending: false }).limit(1).maybeSingle(),
    supabase.from("workflow_usage").select("started_at")
      .eq("workflow_name", "analyze").eq("status", "SUCCESS")
      .order("started_at", { ascending: false }).limit(1).maybeSingle(),
    supabase.from("gemini_calls").select("status")
      .eq("quota_date_pt", quotaDatePt()),
    supabase.from("analysis_jobs").select("created_at")
      .in("status", ["PENDING", "RETRY", "DEFERRED"])
      .order("created_at", { ascending: true }),
    supabase.from("analysis_jobs").select("id", { count: "exact", head: true })
      .eq("last_error_code", "STALE_LOCK"),
    supabase.from("briefs")
      .select("title, status, published_at, generated_at")
      .order("generated_at", { ascending: false }).limit(1).maybeSingle(),
    supabase.from("workflow_usage")
      .select("started_at, status, keepalive_performed, keepalive_status")
      .eq("workflow_name", "backup")
      .order("started_at", { ascending: false }).limit(1).maybeSingle(),
    supabase.from("workflow_usage").select("duration_seconds")
      .gte("started_at", monthStart.toISOString()),
    // 독자 오류 신고 접수함 — 실측 1년간 0건이라 평소에는 0이 정상이다.
    // 0이 아닐 때만 운영 현황에 알림으로 뜬다 (/admin/error-reports = 콘텐츠 관리).
    supabase.from("error_reports").select("id", { count: "exact", head: true })
      .eq("status", "OPEN"),
    supabase.from("sources").select("name, last_failure_at, last_success_at, last_error_message")
      .eq("is_active", true),
    // 사람이 직접 숨긴 콘텐츠 (사유 없음·'중복…'은 배치의 자동 중복 정리라 뺀다)
    supabase.from("published_items").select("id", { count: "exact", head: true })
      .eq("is_visible", false)
      .not("hidden_reason", "is", null)
      .not("hidden_reason", "like", "중복%"),
  ]);

  const jobCounts: Record<string, number> = {};
  for (const row of jobStats.data ?? []) {
    jobCounts[row.status] = (jobCounts[row.status] ?? 0) + 1;
  }

  const geminiOk = (geminiToday.data ?? []).filter((r) => r.status === "OK").length;
  const gemini429 = (geminiToday.data ?? []).filter(
    (r) => r.status === "RATE_LIMITED",
  ).length;

  const pendingList = pendingJobs.data ?? [];
  const oldestPendingMinutes = pendingList.length
    ? Math.floor(
        (Date.now() - new Date(pendingList[0].created_at).getTime()) / 60000,
      )
    : null;

  // GitHub 청구 기준으로 추정한다 (2026-08-29 정정). 종전에는 내부 측정
  // 초의 합만 보여줘서, 화면이 1,791분일 때 GitHub은 이미 2,000분 한도를
  // 넘어 실행을 거부하고 있었다 — 사용자가 착시로 오판할 수 있는 숫자였다.
  // 청구는 잡마다 (내부초 + 준비 오버헤드 ~75초)를 분 단위로 올림해 매긴다.
  // CI 등 미계측 워크플로가 있어 이 추정도 하한이다 — 화면에 '추정' 표기.
  const BILLING_OVERHEAD_SECONDS = 75;
  const monthMinutes = (monthUsage.data ?? []).reduce(
    (sum, r) =>
      sum + Math.ceil(((r.duration_seconds ?? 0) + BILLING_OVERHEAD_SECONDS) / 60),
    0,
  );

  const lastCollectAt = lastCollect.data?.started_at ?? null;
  const staleWarning =
    !lastCollectAt ||
    Date.now() - new Date(lastCollectAt).getTime() > 24 * 3600_000;

  // 최근 실패가 최근 성공보다 나중이면 장기 실패 의심 수집원
  const failing = (failingSources.data ?? []).filter(
    (s) =>
      s.last_failure_at &&
      (!s.last_success_at ||
        new Date(s.last_failure_at) > new Date(s.last_success_at)),
  );

  return {
    collectedToday: collectedToday.count ?? 0,
    publishedToday: publishedToday.count ?? 0,
    excludedTotal: excludedTotal.count ?? 0,
    clusterCount: clusters.count ?? 0,
    jobCounts,
    lastCollectAt,
    lastAnalyzeAt: lastAnalyze.data?.started_at ?? null,
    staleWarning,
    geminiOk,
    gemini429,
    pendingCount: pendingList.length,
    oldestPendingMinutes,
    staleRecoveredCount: staleRecovered.count ?? 0,
    latestBrief: latestBrief.data,
    latestBackup: latestBackup.data,
    monthMinutes,
    budgetMinutes: 1500,
    warnMinutes: 1200,
    openReportCount: openReports.count ?? 0,
    operatorHiddenCount: operatorHidden.count ?? 0,
    failingSources: failing,
  };
}
