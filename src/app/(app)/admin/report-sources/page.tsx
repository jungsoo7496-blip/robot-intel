import Link from "next/link";

import { requireOperator } from "@/lib/auth";
import { createServiceRoleClient } from "@/lib/supabase/server";

import {
  getReportSourceFormOptions,
  type ReportChannelRow,
  type ReportSourceRow,
} from "./actions";
import {
  ChannelAddButton,
  ChannelRow,
  CredentialKeyNotice,
  type ChannelCombos,
} from "./channel-form";
import { CollectReportsNowButton, SourceDeleteButton, SourceForm } from "./source-form";

export const dynamic = "force-dynamic";

function formatDateTime(value: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Seoul",
  });
}

const STATUS_LABELS: Record<string, string> = {
  CANDIDATE: "후보",
  NEEDS_ADAPTER: "adapter 필요",
  READY: "준비됨",
  ACTIVE: "정상",
  PAUSED: "일시중지",
  BROKEN: "고장",
  RETIRED: "종료",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`rounded-md px-2 py-0.5 text-xs font-bold ${
        status === "ACTIVE"
          ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300"
          : status === "BROKEN"
            ? "bg-red-100 text-red-700 dark:bg-red-500/20 dark:text-red-300"
            : "bg-black/8 text-black/60 dark:bg-white/10 dark:text-white/60"
      }`}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

type RunAgg = {
  fetched: number;
  newOcc: number;
  newDoc: number;
  dup: number;
  accessChecked: number;
  usable: number;
  aiQueued: number;
  errors: number;
  runs: number;
};

type SourceRow = ReportSourceRow & {
  consecutive_failures: number;
  last_success_at: string | null;
};

/** (자료 종류 × 검색어) 실행 상태 — 배치가 회전용으로 쓰는 표. */
type KeywordRunRow = {
  channel_id: string;
  keyword_term: string;
  last_run_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
};

const CHANNEL_COLUMNS =
  "id, source_id, channel_key, name, adapter_key, collection_method, enabled, query, " +
  "uses_keywords, retired_at, config_json, fetch_interval_hours, max_pages_per_run, " +
  "max_items_per_run, request_interval_ms, credential_key_name, supports_abstract, " +
  "supports_file_metadata, supports_direct_download, supports_viewer, last_run_at, " +
  "last_success_at, last_error";

/**
 * 보고서 수집원 현황 + 설정 (보고서 스펙 §31, 사용자 요구 2번).
 *
 * 검색어는 이 화면에 없다 — [검색 키워드] 화면의 공용 목록(search_keywords)을
 * 배치가 실행 시점에 (자료 종류 × 검색어)로 곱한다. 여기서는 수집원별 설정
 * (어댑터·필요한 키 이름·페이지 수·건수·요청 간격·수집 주기·하위 구분)만 다룬다.
 */
export default async function ReportSourcesPage() {
  // 레이아웃 검사는 소프트 내비게이션 때 다시 돌지 않는다(Next: 레이아웃은
  // 라우트 이동 시 재렌더되지 않음) — 세션 24시간 만료·계정 비활성화가
  // service role 조회 앞에서 확인되도록 데이터 조회 직전에 다시 검사한다.
  await requireOperator();
  const supabase = createServiceRoleClient();
  const renderedAt = new Date();
  const since = new Date(
    renderedAt.getTime() - 7 * 24 * 60 * 60 * 1000,
  ).toISOString();

  const [sourcesRes, channelsRes, runsRes, queueRes, keywordsRes, keywordRunsRes, options] =
    await Promise.all([
      supabase.from("report_sources").select("*").order("priority").order("name"),
      supabase
        .from("report_source_channels")
        .select(CHANNEL_COLUMNS)
        .order("channel_key"),
      supabase
        .from("report_source_runs")
        .select(
          "source_id, status, fetched_count, new_occurrence_count, new_document_count, duplicate_count, access_checked_count, usable_count, ai_queued_count, error_count",
        )
        .gte("started_at", since),
      supabase
        .from("report_analysis_jobs")
        .select("status")
        .in("status", ["PENDING", "RETRY", "DEFERRED", "FAILED"]),
      // 보고서 수집에 쓰는 공용 검색어 (뉴스 전용 scope='NEWS'는 제외)
      supabase
        .from("search_keywords")
        .select("term, scope, sort_order")
        .in("scope", ["ALL", "REPORTS"])
        .order("sort_order")
        .order("term"),
      supabase
        .from("report_channel_keyword_runs")
        .select("channel_id, keyword_term, last_run_at, last_success_at, last_error"),
      getReportSourceFormOptions(),
    ]);

  const sources = (sourcesRes.data ?? []) as SourceRow[];
  const allChannels = (channelsRes.data ?? []) as unknown as ReportChannelRow[];
  const liveChannels = allChannels.filter((c) => !c.retired_at);
  const retiredChannels = allChannels
    .filter((c) => !!c.retired_at)
    .sort((a, b) => (a.retired_at ?? "").localeCompare(b.retired_at ?? ""));

  const keywordTerms = ((keywordsRes.data ?? []) as { term: string }[]).map((k) => k.term);
  const keywordsUnavailable = !!keywordsRes.error;
  const keywordRuns = (keywordRunsRes.data ?? []) as unknown as KeywordRunRow[];

  // 수집원별 모은 자료 수 — 삭제 가능 여부(0건만 하드 삭제) 판단용
  const occurrenceCounts = await Promise.all(
    sources.map((s) =>
      supabase
        .from("report_occurrences")
        .select("id", { count: "exact", head: true })
        .eq("source_id", s.id)
        .then((r) => [s.id, r.count ?? 0] as const),
    ),
  );
  const occurrenceBySource = new Map(occurrenceCounts);

  // 종류(source_kind) 기존 값 분포 — 추가 폼의 선택 도움말
  const sourceKinds = [...new Set(sources.map((s) => s.source_kind).filter((k): k is string => !!k))].sort();

  const aggBySource = new Map<string, RunAgg>();
  for (const r of runsRes.data ?? []) {
    if (!r.source_id) continue;
    const agg =
      aggBySource.get(r.source_id) ??
      ({
        fetched: 0, newOcc: 0, newDoc: 0, dup: 0, accessChecked: 0,
        usable: 0, aiQueued: 0, errors: 0, runs: 0,
      } as RunAgg);
    agg.fetched += r.fetched_count;
    agg.newOcc += r.new_occurrence_count;
    agg.newDoc += r.new_document_count;
    agg.dup += r.duplicate_count;
    agg.accessChecked += r.access_checked_count;
    agg.usable += r.usable_count;
    agg.aiQueued += r.ai_queued_count;
    agg.errors += r.error_count + (r.status === "FAILED" ? 1 : 0);
    agg.runs += 1;
    aggBySource.set(r.source_id, agg);
  }

  const queueCounts: Record<string, number> = {};
  for (const j of queueRes.data ?? []) {
    queueCounts[j.status] = (queueCounts[j.status] ?? 0) + 1;
  }

  // (자료 종류 × 검색어) 조합 집계 — repository.fetch_due_tasks 와 같은 판정:
  // 실행 이력이 없거나 수집 주기가 지난 조합이 다음 수집 대상.
  const now = renderedAt.getTime();
  const day = 24 * 60 * 60 * 1000;
  const runByCombo = new Map<string, KeywordRunRow>();
  for (const r of keywordRuns) runByCombo.set(`${r.channel_id}|${r.keyword_term}`, r);

  const combosByChannel = new Map<string, ChannelCombos>();
  for (const c of liveChannels) {
    // 검색어 미사용 자료 종류(prism)는 keyword_term='' 한 줄로 돈다
    const terms = c.uses_keywords ? keywordTerms : [""];
    let ran24h = 0;
    let due = 0;
    let lastError: string | null = null;
    let lastErrorAt = 0;
    for (const term of terms) {
      const run = runByCombo.get(`${c.id}|${term}`);
      const ranAt = run?.last_run_at ? Date.parse(run.last_run_at) : null;
      if (ranAt !== null && now - ranAt < day) ran24h += 1;
      if (ranAt === null || ranAt < now - c.fetch_interval_hours * 60 * 60 * 1000) due += 1;
      if (run?.last_error && ranAt !== null && ranAt >= lastErrorAt) {
        lastError = run.last_error;
        lastErrorAt = ranAt;
      }
    }
    combosByChannel.set(c.id, { total: terms.length, ran24h, due, lastError });
  }

  // 실행 조합 수 — 검색어를 쓰는 자료 종류만 검색어 개수만큼 늘어나고, 검색어를 쓰지
  // 않는 자료 종류(prism)는 자료 종류당 1개다. 화면 문구가 이 셈을 그대로 보여준다.
  const enabledChannels = liveChannels.filter((c) => c.enabled);
  const enabledKeywordChannels = enabledChannels.filter((c) => c.uses_keywords).length;
  const enabledPlainChannels = enabledChannels.length - enabledKeywordChannels;
  const totalCombos = enabledKeywordChannels * keywordTerms.length + enabledPlainChannels;
  // 읽는 사람이 직접 계산해 봐도 맞도록 곱셈·덧셈을 그대로 적는다
  // (예: 7개 × 7개 + 1개 = 50개 — prism 한 자료 종류는 검색어를 곱하지 않는다)
  const comboFormula =
    `검색어를 쓰는 자료 종류 ${enabledKeywordChannels}개 × 검색어 ${keywordTerms.length}개` +
    (enabledPlainChannels > 0
      ? ` + 검색어 없이 도는 자료 종류 ${enabledPlainChannels}개`
      : "") +
    ` = 모두 ${totalCombos}개`;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">보고서 수집원</h1>
        {/* 정기 배치(매일 06:35)와 같은 collect-reports 워크플로를 즉시 실행 */}
        <CollectReportsNowButton />
      </div>
      <p className="text-sm text-black/60 dark:text-white/60">
        보고서를 가져오는 사이트별 현황입니다 (최근 7일). 무엇을 찾을지는{" "}
        <Link href="/admin/search-keywords" className="underline">
          검색 키워드
        </Link>
        에서, 사이트마다 어떻게 가져올지는 아래에서 정합니다.
      </p>

      {channelsRes.error && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300">
          <strong>사이트 설정을 읽지 못했습니다.</strong> 아래 목록이 비어 보입니다 —
          수집 자체는 평소대로 계속됩니다. 잠시 뒤 새로고침해 보시고, 계속 이러면
          데이터베이스 변경이 아직 적용되지 않은 것일 수 있습니다(관리자 문의).
          <span className="mt-1 block text-xs opacity-70">
            기술 정보: {channelsRes.error.message}
          </span>
        </div>
      )}

      {/* ---- 검색어는 공용 목록 (요구 2-2) ---- */}
      <div className="rounded-lg border border-black/10 p-3 dark:border-white/15">
        <div className="text-sm font-semibold">지금 이 검색어로 찾습니다</div>
        {keywordsUnavailable ? (
          <p className="mt-1 text-sm text-red-600 dark:text-red-400">
            검색어 목록을 읽지 못했습니다 — 잠시 뒤 새로고침해 보세요. 수집은 평소대로
            계속됩니다.
          </p>
        ) : keywordTerms.length === 0 ? (
          <p className="mt-1 text-sm text-red-600 dark:text-red-400">
            보고서용 검색어가 하나도 없습니다 — 검색어를 넣기 전에는 아래 수집원들이 아무것도
            찾지 못합니다 (검색어를 쓰지 않는 수집원은 예외).
          </p>
        ) : (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {keywordTerms.map((t) => (
              <span
                key={t}
                className="rounded-md bg-black/5 px-2 py-0.5 text-sm dark:bg-white/10"
              >
                {t}
              </span>
            ))}
          </div>
        )}
        <p className="mt-2 text-xs text-black/55 dark:text-white/55">
          검색어는{" "}
          <Link href="/admin/search-keywords" className="underline underline-offset-2">
            검색 키워드
          </Link>{" "}
          화면에서 한 번만 정하면 여기 모든 수집원에 똑같이 적용됩니다. 이 화면에서는
          검색어를 고치지 않습니다. 지금 도는 조합은 {comboFormula}이고, 한 번의
          수집에서 다 못 돌면 오래 기다린 조합부터 차례로 돕니다.
        </p>
      </div>

      <div className="flex flex-wrap gap-3 text-sm">
        {(["PENDING", "RETRY", "DEFERRED", "FAILED"] as const).map((s) => (
          <div
            key={s}
            className="rounded-lg border border-black/10 px-3 py-2 dark:border-white/15"
          >
            <span className="text-black/55 dark:text-white/55">분석 {s}</span>{" "}
            <span className="font-semibold">{queueCounts[s] ?? 0}</span>
          </div>
        ))}
      </div>

      <div className="overflow-x-auto rounded-lg border border-black/10 dark:border-white/15">
        <table className="w-full min-w-[980px] text-sm">
          <thead className="border-b border-black/10 text-left text-black/60 dark:border-white/15 dark:text-white/60">
            <tr>
              <th className="p-3">소스</th>
              <th className="p-3">상태</th>
              <th className="p-3">자료 종류</th>
              <th className="p-3">마지막 성공</th>
              <th className="p-3">수집</th>
              <th className="p-3">신규 문서</th>
              <th className="p-3">중복</th>
              <th className="p-3">이용가능률</th>
              <th className="p-3">AI 큐</th>
              <th className="p-3">오류</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => {
              const chs = liveChannels.filter((c) => c.source_id === s.id);
              const enabled = chs.filter((c) => c.enabled).length;
              const agg = aggBySource.get(s.id);
              const usableRate =
                agg && agg.accessChecked > 0
                  ? `${Math.round((agg.usable / agg.accessChecked) * 100)}%`
                  : "—";
              const broken = s.consecutive_failures > 0;
              const chError =
                chs.find((c) => c.last_error)?.last_error ??
                chs.map((c) => combosByChannel.get(c.id)?.lastError).find((e) => !!e) ??
                null;
              return (
                <tr
                  key={s.id}
                  className="border-b border-black/5 last:border-0 dark:border-white/10"
                >
                  <td className="p-3">
                    <div className="font-medium">{s.name}</div>
                    <div className="text-xs text-black/45 dark:text-white/45">
                      {s.source_key}
                      {s.adapter_key
                        ? ` · adapter: ${s.adapter_key}`
                        : " · adapter 없음"}
                    </div>
                  </td>
                  <td className="p-3">
                    <StatusBadge status={s.status} />
                  </td>
                  <td className="p-3">
                    {enabled}/{chs.length}
                    {chError && (
                      <div className="max-w-[180px] truncate text-xs text-red-600 dark:text-red-400">
                        {chError}
                      </div>
                    )}
                  </td>
                  <td className={`p-3 ${broken ? "text-red-600" : ""}`}>
                    {formatDateTime(s.last_success_at)}
                    {broken && (
                      <div className="text-xs">연속 실패 {s.consecutive_failures}</div>
                    )}
                  </td>
                  <td className="p-3">{agg?.fetched ?? 0}</td>
                  <td className="p-3">
                    {agg?.newDoc ?? 0}
                    <span className="text-xs text-black/45 dark:text-white/45">
                      {" "}
                      / occ {agg?.newOcc ?? 0}
                    </span>
                  </td>
                  <td className="p-3">{agg?.dup ?? 0}</td>
                  <td className="p-3">{usableRate}</td>
                  <td className="p-3">{agg?.aiQueued ?? 0}</td>
                  <td className={`p-3 ${agg?.errors ? "text-red-600" : ""}`}>
                    {agg?.errors ?? 0}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-black/45 dark:text-white/45">
        이용가능률 = 접근성 검사 대비 DIRECT_DOWNLOAD·SOURCE_DOWNLOAD·VIEW_ONLY
        비율 · 시각은 한국시간
      </p>

      {/* ---------------- 수집원별 설정 ---------------- */}
      <h2 className="pt-4 text-lg font-semibold">수집원별 설정</h2>
      <p className="text-sm text-black/60 dark:text-white/60">
        배치는 상태가 &ldquo;정상(ACTIVE)&rdquo;인 수집원의 &ldquo;활성&rdquo; 자료 종류를,
        검색어마다 한 번씩 수집 주기가 지났을 때 실행합니다(매일 06:35, 또는 위
        &ldquo;지금 실행&rdquo;). 한 자료 종류를 당장 다시 돌리고 싶으면 &ldquo;지금 실행
        대상으로&rdquo;를 누른 뒤 &ldquo;보고서 수집 지금 실행&rdquo;을 누르세요. 수집원
        삭제는 모은 자료가 없을 때만 가능하고, 자료가 있으면 &ldquo;종료&rdquo;로
        전환합니다.
      </p>
      <CredentialKeyNotice />

      <div className="flex flex-wrap gap-2">
        <SourceForm mode="create" options={options} sourceKinds={sourceKinds} />
      </div>

      {sources.map((s) => {
        const chs = liveChannels.filter((c) => c.source_id === s.id);
        const sourceInfo = {
          id: s.id,
          name: s.name,
          sourceKey: s.source_key,
          status: s.status,
          adapterKey: s.adapter_key,
        };
        const initial: ReportSourceRow = {
          id: s.id,
          source_key: s.source_key,
          name: s.name,
          base_url: s.base_url,
          source_kind: s.source_kind,
          status: s.status,
          priority: s.priority,
          owner_org: s.owner_org,
          description: s.description,
          notes: s.notes,
          adapter_key: s.adapter_key,
        };
        const usesKeywords = chs.some((c) => c.uses_keywords);
        return (
          <section
            key={s.id}
            className="space-y-3 rounded-lg border border-black/10 p-4 dark:border-white/15"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold">{s.name}</span>
                  <StatusBadge status={s.status} />
                  {chs.length > 0 && !usesKeywords && (
                    <span className="rounded-md bg-black/8 px-2 py-0.5 text-xs font-medium text-black/60 dark:bg-white/10 dark:text-white/60">
                      검색어 없이 전체를 봅니다
                    </span>
                  )}
                </div>
                <div className="text-xs text-black/45 dark:text-white/45">
                  {s.source_key} · adapter {s.adapter_key ?? "없음"} ·{" "}
                  {s.source_kind ?? "종류 미지정"} · 우선순위 {s.priority} ·{" "}
                  {s.owner_org ?? "기관 미지정"} · 모은 자료{" "}
                  {(occurrenceBySource.get(s.id) ?? 0).toLocaleString("ko-KR")}건
                </div>
                {s.base_url && (
                  <a
                    href={s.base_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block max-w-md truncate text-xs text-black/45 underline-offset-2 hover:underline dark:text-white/45"
                  >
                    {s.base_url}
                  </a>
                )}
                {s.description && (
                  <p className="max-w-2xl text-xs text-black/60 dark:text-white/60">
                    {s.description}
                  </p>
                )}
                {s.notes && (
                  <p className="max-w-2xl text-xs text-black/50 dark:text-white/50">
                    메모: {s.notes}
                  </p>
                )}
              </div>
              <SourceDeleteButton
                source={initial}
                occurrenceCount={occurrenceBySource.get(s.id) ?? 0}
              />
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <SourceForm
                mode="edit"
                initial={initial}
                options={options}
                sourceKinds={sourceKinds}
              />
            </div>

            <div className="overflow-x-auto rounded-lg border border-black/10 dark:border-white/15">
              <table className="w-full min-w-[1180px] text-sm">
                <thead className="border-b border-black/10 text-left text-black/60 dark:border-white/15 dark:text-white/60">
                  <tr>
                    <th className="p-3">자료 종류</th>
                    <th className="p-3">검색어 사용</th>
                    <th className="p-3">수집 조합</th>
                    <th className="p-3">활성</th>
                    <th className="p-3">마지막 실행</th>
                    <th className="p-3">마지막 성공</th>
                    <th className="p-3">오류</th>
                    <th className="p-3">필요 키 이름</th>
                    <th className="p-3">조치</th>
                  </tr>
                </thead>
                <tbody>
                  {chs.length === 0 ? (
                    <tr>
                      <td colSpan={9} className="p-3 text-sm text-black/50 dark:text-white/50">
                        자료 종류가 없어 아무것도 수집하지 않습니다 — 아래
                        &ldquo;자료 종류 추가&rdquo;로 하나 만드세요.
                      </td>
                    </tr>
                  ) : (
                    chs.map((c) => (
                      <ChannelRow
                        key={c.id}
                        channel={c}
                        source={sourceInfo}
                        options={options}
                        keywordTerms={keywordTerms}
                        combos={
                          combosByChannel.get(c.id) ?? {
                            total: 0,
                            ran24h: 0,
                            due: 0,
                            lastError: null,
                          }
                        }
                        lastRunLabel={formatDateTime(c.last_run_at)}
                        lastSuccessLabel={formatDateTime(c.last_success_at)}
                      />
                    ))
                  )}
                </tbody>
              </table>
            </div>

            <div className="flex flex-wrap gap-2">
              <ChannelAddButton
                source={sourceInfo}
                options={options}
                keywordTerms={keywordTerms}
              />
            </div>
          </section>
        );
      })}

      {/* ---------------- 검색 키워드로 통합된 옛 채널 ---------------- */}
      {retiredChannels.length > 0 && (
        <details className="rounded-lg border border-black/10 p-4 dark:border-white/15">
          <summary className="cursor-pointer text-sm font-semibold">
            검색 키워드로 통합된 옛 채널 {retiredChannels.length}개 (수집 기록 보존용, 실행 안 함)
          </summary>
          <p className="mt-2 text-sm text-black/60 dark:text-white/60">
            예전에는 &lsquo;로봇&rsquo;·&lsquo;휴머노이드&rsquo;처럼 검색어마다 채널을 따로
            만들었습니다. 지금은 검색어를 한 곳(
            <Link href="/admin/search-keywords" className="underline underline-offset-2">
              검색 키워드
            </Link>
            )에 모아 두고 자료 종류마다 곱해 쓰기 때문에 옛 채널은 더 이상 실행하지 않습니다.
            이미 모은 자료가 이 채널들을 가리키고 있어 지우지 않고 남겨 둔 것이니, 통계에
            옛 이름이 보여도 오류가 아닙니다.
          </p>
          <div className="mt-3 overflow-x-auto rounded-lg border border-black/10 dark:border-white/15">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="border-b border-black/10 text-left text-black/60 dark:border-white/15 dark:text-white/60">
                <tr>
                  <th className="p-3">수집원</th>
                  <th className="p-3">옛 채널</th>
                  <th className="p-3">옛 검색어</th>
                  <th className="p-3">마지막 성공</th>
                  <th className="p-3">정리한 때</th>
                </tr>
              </thead>
              <tbody>
                {retiredChannels.map((c) => {
                  const src = sources.find((s) => s.id === c.source_id);
                  return (
                    <tr
                      key={c.id}
                      className="border-b border-black/5 last:border-0 dark:border-white/10"
                    >
                      <td className="p-3">{src?.name ?? "—"}</td>
                      <td className="p-3">
                        <div>{c.name}</div>
                        <div className="text-xs text-black/45 dark:text-white/45">
                          {c.channel_key}
                        </div>
                      </td>
                      <td className="p-3">
                        {c.query ?? (
                          <span className="text-black/40 dark:text-white/40">—</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap p-3">
                        {formatDateTime(c.last_success_at)}
                      </td>
                      <td className="whitespace-nowrap p-3">{formatDateTime(c.retired_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}
