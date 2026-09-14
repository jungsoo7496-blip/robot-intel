import Link from "next/link";

import { requireOperator } from "@/lib/auth";
import { createServiceRoleClient } from "@/lib/supabase/server";

import {
  KEYWORD_COUNT_WARN,
  MAX_KEYWORDS,
  SCOPE_LABELS,
  googleNewsRssUrl,
  guessKeywordOfSource,
  isMissingTableError,
  scopeUsesNews,
  scopeUsesReports,
  type NewsSearchTargetRow,
  type NewsTargetKey,
  type SearchKeywordRow,
  type SourceKeyword,
} from "./keyword-fields";
import { DeleteKeywordButton, KeywordForm } from "./keyword-form";
import { TargetForm } from "./target-form";

export const dynamic = "force-dynamic";

/**
 * 검색 키워드 (요구 1-1·1-2·2-1·2-2).
 *
 * 네이버·구글 뉴스 검색어와 보고서 수집원 검색어를 한 목록으로 관리한다.
 * 이 화면은 표 2개(search_keywords · news_search_targets)만 고치고,
 * 실제 수집원 행(sources)은 수집 배치가 이 목록을 보고 만든다 —
 * 그래서 모든 안내가 "다음 수집부터 반영"이라고 말한다.
 */

/** 유입량 통계를 낼 기간 — 배치 실행 편차를 흡수할 만큼 넉넉하게. */
const STATS_DAYS = 7;

type NewsSourceRow = {
  id: string;
  name: string;
  url: string;
  fetch_method: string;
  adapter_config: Record<string, unknown> | null;
  is_active: boolean;
  managed_by?: string | null;
  search_target?: string | null;
  keyword_term?: string | null;
};

type KeywordStat = {
  /** 검색어별 지금까지 모은 기사 수 (raw_items). */
  collected: number;
  /** 최근 STATS_DAYS일 유입 (대상별). */
  recentByTarget: Record<NewsTargetKey, number>;
  /** 이 검색어로 만들어진 수집원이 sources에 있는가. */
  hasSource: boolean;
};

function perDay(count: number): number {
  return Math.round(count / STATS_DAYS);
}

function num(value: number): string {
  return value.toLocaleString("ko-KR");
}

/**
 * 수집원 목록을 읽는다.
 *
 * 마이그레이션 26 적용 전에는 managed_by·keyword_term 컬럼이 없어 조회가
 * 실패하므로, 그때는 기존 컬럼만 읽고 검색어를 행 모양에서 되짚는다
 * (guessKeywordOfSource). 화면이 마이그레이션 전후 모두 열려야 한다.
 */
async function loadNewsSources(
  supabase: ReturnType<typeof createServiceRoleClient>,
): Promise<NewsSourceRow[]> {
  const withTags = await supabase
    .from("sources")
    .select(
      "id, name, url, fetch_method, adapter_config, is_active, managed_by, search_target, keyword_term",
    );
  if (!withTags.error) return (withTags.data ?? []) as NewsSourceRow[];

  const basic = await supabase
    .from("sources")
    .select("id, name, url, fetch_method, adapter_config, is_active");
  return (basic.data ?? []) as NewsSourceRow[];
}

/**
 * 검색어를 쓰는 보고서 수집원이 지금 몇 곳인지.
 *
 * 마이그레이션 26 적용 후에는 uses_keywords로, 적용 전에는 '검색어(query)가
 * 들어 있는 채널'로 센다. 화면 맨 위 안내에 고정 숫자 대신 실제 값을 쓴다.
 */
async function loadReportSourceCount(
  supabase: ReturnType<typeof createServiceRoleClient>,
): Promise<number | null> {
  const withFlag = await supabase
    .from("report_source_channels")
    .select("id, source_id")
    .eq("enabled", true)
    .eq("uses_keywords", true)
    .is("retired_at", null);
  const res = withFlag.error
    ? await supabase
        .from("report_source_channels")
        .select("id, source_id")
        .eq("enabled", true)
        .not("query", "is", null)
    : withFlag;
  if (res.error || !res.data) return null;
  const rows = res.data as { source_id: string }[];
  return new Set(rows.map((r) => r.source_id)).size;
}

export default async function AdminSearchKeywordsPage() {
  // 레이아웃 검사만 믿지 않는다 — 서비스 키로 읽기 전에 여기서 다시 확인한다.
  await requireOperator();
  const supabase = createServiceRoleClient();
  const since = new Date();
  since.setDate(since.getDate() - STATS_DAYS);
  const sinceIso = since.toISOString();

  const [keywordRes, targetRes, sourceRows, reportSourceCount] = await Promise.all([
    supabase
      .from("search_keywords")
      .select("id, term, alt_terms, scope, sort_order, note")
      .order("sort_order")
      .order("term"),
    supabase
      .from("news_search_targets")
      .select(
        "target_key, name, is_active, priority, fetch_interval_minutes, display, source_type, country_region, language",
      )
      .order("target_key"),
    loadNewsSources(supabase),
    loadReportSourceCount(supabase),
  ]);

  const setupPending =
    isMissingTableError(keywordRes.error) || isMissingTableError(targetRes.error);

  // alt_terms는 NOT NULL DEFAULT '{}'이지만, 표시 단계에서 null을 만나도
  // 화면이 통째로 죽지 않게 빈 배열로 맞춘다.
  const keywords = ((keywordRes.data ?? []) as SearchKeywordRow[]).map((k) => ({
    ...k,
    alt_terms: k.alt_terms ?? [],
  }));
  const targets = (targetRes.data ?? []) as NewsSearchTargetRow[];

  // 검색어별 유입 통계 — 수집원 행마다 건수를 세어 검색어 단위로 합친다.
  // (source_id, fetched_at) 인덱스가 있어 head count는 가볍다.
  const keywordSources: { row: NewsSourceRow; kw: SourceKeyword }[] = [];
  for (const row of sourceRows) {
    const kw = guessKeywordOfSource(row);
    if (kw) keywordSources.push({ row, kw });
  }

  const counted = await Promise.all(
    keywordSources.map(async ({ row, kw }) => {
      const [{ count: total }, { count: recent }] = await Promise.all([
        supabase
          .from("raw_items")
          .select("id", { count: "exact", head: true })
          .eq("source_id", row.id),
        supabase
          .from("raw_items")
          .select("id", { count: "exact", head: true })
          .eq("source_id", row.id)
          .gte("fetched_at", sinceIso),
      ]);
      return { kw, total: total ?? 0, recent: recent ?? 0 };
    }),
  );

  const statByTerm = new Map<string, KeywordStat>();
  const recentByTarget: Record<NewsTargetKey, number> = { naver: 0, google: 0 };
  for (const { kw, total, recent } of counted) {
    const key = kw.term.toLocaleLowerCase();
    const stat = statByTerm.get(key) ?? {
      collected: 0,
      recentByTarget: { naver: 0, google: 0 },
      hasSource: false,
    };
    stat.collected += total;
    stat.recentByTarget[kw.target] += recent;
    stat.hasSource = true;
    statByTerm.set(key, stat);
    recentByTarget[kw.target] += recent;
  }

  // 검색어가 너무 많을 때 띄우는 경고에만 쓰는 값 — 전체 유입과 AI 분석 대기.
  const [rawRecent, jobsPending] = await Promise.all([
    supabase
      .from("raw_items")
      .select("id", { count: "exact", head: true })
      .gte("fetched_at", sinceIso),
    supabase
      .from("analysis_jobs")
      .select("id", { count: "exact", head: true })
      .eq("status", "PENDING"),
  ]);
  const rawPerDay = perDay(rawRecent.count ?? 0);
  const pendingJobs = jobsPending.count ?? 0;

  const newsKeywords = keywords.filter((k) => scopeUsesNews(k.scope));
  const reportKeywords = keywords.filter((k) => scopeUsesReports(k.scope));

  // 검색어 하나가 실제로 얼마나 모으는지 — 추정이 아니라 실측 범위만 말한다.
  // 검색어 목록이 아직 없으면(마이그레이션 전) 지금 도는 수집원에서 되짚는다.
  const measuredKeys =
    newsKeywords.length > 0
      ? newsKeywords.map((k) => k.term.toLocaleLowerCase())
      : [...statByTerm.keys()];
  const measured = measuredKeys
    .map((key) => {
      const s = statByTerm.get(key);
      if (!s?.hasSource) return null;
      return perDay(s.recentByTarget.naver + s.recentByTarget.google);
    })
    .filter((n): n is number => n !== null && n > 0)
    .sort((a, b) => a - b);
  const measuredLow = measured.length ? measured[0] : null;
  const measuredHigh = measured.length ? measured[measured.length - 1] : null;

  const overWarn = keywords.length > KEYWORD_COUNT_WARN;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">검색 키워드</h1>
        <Link
          href="/admin"
          className="rounded border border-black/15 px-2 py-1 text-sm hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
        >
          ← 운영 현황
        </Link>
      </div>

      <p className="text-sm text-black/60 dark:text-white/60">
        여기 넣은 검색어로 <strong>네이버·구글 뉴스</strong>와{" "}
        <strong>
          보고서 수집원
          {reportSourceCount !== null ? ` ${reportSourceCount}곳` : ""}
        </strong>
        을 함께 찾습니다. 새 검색어는 <strong>다음 수집부터</strong>{" "}
        반영됩니다(뉴스 하루 3회 · 보고서 하루 1회). RSS 주소를 직접 등록하거나
        뉴스 수집을 바로 돌리려면{" "}
        <Link href="/admin/sources" className="underline">
          뉴스 수집원
        </Link>{" "}
        화면을 쓰세요.
      </p>

      {setupPending && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          검색 키워드 표가 아직 데이터베이스에 없습니다. 마이그레이션이 적용될
          때까지는 검색어를 추가·삭제할 수 없습니다(수집은 평소대로 됩니다).
        </div>
      )}
      {!setupPending && keywordRes.error && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
          검색어 목록을 읽지 못했습니다: {keywordRes.error.message}
        </div>
      )}

      {/* ── 검색어 목록 ─────────────────────────────── */}
      <section className="space-y-3 rounded-lg border border-black/10 p-4 dark:border-white/15">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-lg font-semibold">
            검색어 목록{" "}
            <span className="text-sm font-normal text-black/50 dark:text-white/50">
              {keywords.length}개 / 최대 {MAX_KEYWORDS}개
            </span>
          </h2>
          <span className="text-xs text-black/50 dark:text-white/50">
            뉴스에 쓰는 검색어 {newsKeywords.length}개 · 보고서에 쓰는 검색어{" "}
            {reportKeywords.length}개
          </span>
        </div>

        {overWarn && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
            검색어 {keywords.length}개 — 지금 하루 약 {num(rawPerDay)}건 수집 ·
            AI 분석 대기 {num(pendingJobs)}건입니다. 겹치는 검색어는 지우는 편이
            좋습니다.
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="w-full min-w-[880px] text-sm">
            <thead className="border-b border-black/10 text-left text-black/60 dark:border-white/15 dark:text-white/60">
              <tr>
                <th className="p-2">검색어</th>
                <th className="p-2">같은 뜻 다른 표기 (구글에서만)</th>
                <th className="p-2">쓰는 곳</th>
                <th className="p-2">최근 {STATS_DAYS}일 유입 (하루 평균)</th>
                <th className="p-2">지금까지 모은 기사</th>
                <th className="p-2"></th>
              </tr>
            </thead>
            <tbody>
              {keywords.map((k) => {
                const stat = statByTerm.get(k.term.toLocaleLowerCase());
                const naver = stat ? perDay(stat.recentByTarget.naver) : 0;
                const google = stat ? perDay(stat.recentByTarget.google) : 0;
                return (
                  <tr
                    key={k.id}
                    className="border-b border-black/5 last:border-0 dark:border-white/10"
                  >
                    <td className="p-2">
                      <div className="font-medium">{k.term}</div>
                      {k.note && (
                        <div className="max-w-xs truncate text-xs text-black/40 dark:text-white/40">
                          {k.note}
                        </div>
                      )}
                    </td>
                    <td className="p-2">
                      {k.alt_terms.length ? (
                        <span className="text-xs">{k.alt_terms.join(", ")}</span>
                      ) : (
                        <span className="text-black/30 dark:text-white/30">—</span>
                      )}
                      {scopeUsesNews(k.scope) && (
                        <div
                          className="max-w-xs truncate font-mono text-[11px] text-black/35 dark:text-white/35"
                          title={googleNewsRssUrl(k.term, k.alt_terms)}
                        >
                          {googleNewsRssUrl(k.term, k.alt_terms)}
                        </div>
                      )}
                    </td>
                    <td className="p-2">{SCOPE_LABELS[k.scope]}</td>
                    <td className="p-2">
                      {scopeUsesNews(k.scope) ? (
                        stat?.hasSource ? (
                          <span>
                            <strong>{num(naver + google)}건</strong>{" "}
                            <span className="text-xs text-black/45 dark:text-white/45">
                              네이버 {num(naver)} · 구글 {num(google)}
                            </span>
                          </span>
                        ) : (
                          <span className="text-black/40 dark:text-white/40">
                            다음 수집부터
                          </span>
                        )
                      ) : (
                        <span className="text-black/30 dark:text-white/30">
                          뉴스에는 쓰지 않음
                        </span>
                      )}
                    </td>
                    <td className="p-2">
                      {stat ? (
                        `${num(stat.collected)}건`
                      ) : (
                        <span className="text-black/30 dark:text-white/30">—</span>
                      )}
                    </td>
                    <td className="p-2">
                      <DeleteKeywordButton
                        id={k.id}
                        term={k.term}
                        collectedCount={stat?.collected ?? 0}
                      />
                    </td>
                  </tr>
                );
              })}
              {keywords.length === 0 && (
                <tr>
                  <td
                    colSpan={6}
                    className="p-3 text-black/50 dark:text-white/50"
                  >
                    등록된 검색어가 없습니다.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <p className="text-xs text-black/50 dark:text-white/50">
          검색어를 지워도 이미 모은 기사는 그대로 남고, 같은 검색어를 다시 넣으면
          이어서 수집됩니다. 고치기는 없으니 &lsquo;쓰는 곳&rsquo;을 바꾸려면
          지운 뒤 다시 추가하세요.
        </p>

        <div className="border-t border-black/10 pt-3 dark:border-white/15">
          <h3 className="text-sm font-semibold">검색어 추가</h3>
          {measuredLow !== null && measuredHigh !== null && (
            <p className="mb-2 text-xs text-black/50 dark:text-white/50">
              지금 검색어 하나가 하루{" "}
              {measuredLow === measuredHigh
                ? `${num(measuredLow)}건`
                : `${num(measuredLow)}~${num(measuredHigh)}건`}
              을 모으고 있습니다(최근 {STATS_DAYS}일 실측).
            </p>
          )}
          <KeywordForm disabled={setupPending} />
        </div>
      </section>

      {/* ── 검색 대상 설정 ─────────────────────────── */}
      <section className="space-y-3">
        <div>
          <h2 className="text-lg font-semibold">검색 대상 설정</h2>
          <p className="mt-1 text-sm text-black/60 dark:text-white/60">
            여기 값은 그 대상의 <strong>모든 검색어</strong>에 똑같이
            적용됩니다. 보고서 쪽 설정은{" "}
            <Link href="/admin/report-sources" className="underline">
              보고서 수집원
            </Link>{" "}
            화면에 있습니다.
          </p>
        </div>
        {targets.length === 0 ? (
          <p className="text-sm text-black/50 dark:text-white/50">
            {setupPending
              ? "마이그레이션이 적용되면 네이버·구글 설정 카드가 여기에 표시됩니다."
              : "네이버·구글 설정을 읽지 못했습니다. 화면을 새로고침해 주세요."}
          </p>
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {targets.map((t) => (
              <TargetForm
                key={t.target_key}
                target={t}
                newsKeywordCount={newsKeywords.length}
                perDay={
                  recentByTarget[t.target_key] > 0
                    ? perDay(recentByTarget[t.target_key])
                    : null
                }
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
