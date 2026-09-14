import { ChipFilter } from "@/components/chip-filter";
import { formatDateDot } from "@/components/item-card";
import { Pagination } from "@/components/pagination";
import {
  ACCESS_LABELS,
  REPORT_ROBOT_FIELDS,
  REPORT_TYPES,
  getPublishedReports,
  getReportFilterOptions,
  type ReportFilters,
  type ReportRow,
} from "@/lib/reports";
import { ROBOT_FIELD_SHORT } from "@/types/analysis";

export const dynamic = "force-dynamic";

type SearchParams = { [key: string]: string | undefined };

// 자료이용·출처 필터는 제거 (사용자 지시 2026-08-08) — 검색+칩 3종만
const FILTER_KEYS = ["q", "type", "field", "year"] as const;

function buildQuery(filters: ReportFilters, page?: number): string {
  const params = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    const value = filters[key];
    if (value) params.set(key, value);
  }
  if (page && page > 1) params.set("page", String(page));
  const s = params.toString();
  return s ? `?${s}` : "";
}

/** 상태별 원문 버튼 (스펙 §29 — 서버를 download proxy로 쓰지 않는다). */
function AccessButton({ row }: { row: ReportRow }) {
  const occ = row.occurrence;
  if (!occ) return null;
  const label = ACCESS_LABELS[occ.access_status];
  if (!label) return null;
  const href =
    occ.access_status === "DIRECT_DOWNLOAD"
      ? (occ.candidate_download_url ?? occ.detail_url)
      : occ.detail_url;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 rounded-md border border-black/15 px-2.5 py-1 text-xs font-medium hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
    >
      {label}
      {occ.access_status === "DIRECT_DOWNLOAD" && occ.file_format && (
        <span className="text-black/45 dark:text-white/45">
          {occ.file_format.split("/")[0]}
        </span>
      )}
      <span aria-hidden>↗</span>
    </a>
  );
}

export default async function ReportsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await searchParams;
  const filters: ReportFilters = {
    q: params.q,
    type: params.type,
    field: params.field,
    year: params.year,
  };
  const page = Number(params.page ?? "1") || 1;

  const [{ rows, total, pageSize }, options] = await Promise.all([
    getPublishedReports(filters, page),
    getReportFilterOptions(),
  ]);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const hasFilter = FILTER_KEYS.some((k) => filters[k]);

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold">정책·동향 보고서</h1>
        <span className="text-sm text-black/50 dark:text-white/50">
          공개 {total}건
        </span>
      </div>
      <p className="text-sm text-black/60 dark:text-white/60">
        정부·공공기관 및 국책연구기관에서 공개한 로봇 관련 정책·산업·기술
        보고서를 모아 제공합니다. 원문은 각 기관 공식 페이지에서 받습니다.
      </p>

      {/* 검색 + 칩 필터 (최신 동향과 같은 클릭 방식 — 사용자 지시) */}
      <div className="space-y-2.5 rounded-lg border border-black/10 p-3 dark:border-white/15">
        <form method="GET" className="flex items-center gap-2">
          <div className="relative w-full max-w-sm">
            <span
              aria-hidden
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-black/40 dark:text-white/40"
            >
              ⌕
            </span>
            <input
              type="search"
              name="q"
              defaultValue={filters.q ?? ""}
              placeholder="보고서 제목·발간기관 검색"
              className="w-full rounded-full border border-black/15 bg-transparent py-1.5 pl-8 pr-3 text-sm outline-none focus:border-black/40 dark:border-white/20 dark:focus:border-white/50"
            />
          </div>
          {/* 검색해도 선택한 칩은 유지 */}
          {filters.type && <input type="hidden" name="type" value={filters.type} />}
          {filters.field && (
            <input type="hidden" name="field" value={filters.field} />
          )}
          {filters.year && <input type="hidden" name="year" value={filters.year} />}
          <button
            type="submit"
            className="shrink-0 rounded-full bg-foreground px-4 py-1.5 text-sm font-medium text-background hover:opacity-90"
          >
            검색
          </button>
          {hasFilter && (
            <a
              href="/reports"
              className="shrink-0 text-sm text-black/50 underline-offset-2 hover:underline dark:text-white/50"
            >
              초기화
            </a>
          )}
        </form>

        <ChipFilter
          basePath="/reports"
          paramName="type"
          label="자료유형"
          options={REPORT_TYPES}
          params={params}
        />
        <ChipFilter
          basePath="/reports"
          paramName="field"
          label="로봇분야"
          options={REPORT_ROBOT_FIELDS}
          params={params}
          labels={ROBOT_FIELD_SHORT}
        />
        {options.years.length > 1 && (
          <ChipFilter
            basePath="/reports"
            paramName="year"
            label="발행연도"
            options={options.years.map(String)}
            params={params}
            scrollable
          />
        )}
      </div>

      {rows.length === 0 ? (
        <div className="rounded-lg border border-dashed border-black/20 p-8 text-center text-sm text-black/50 dark:border-white/25 dark:text-white/50">
          {hasFilter
            ? "조건에 맞는 보고서가 없습니다."
            : "공개된 보고서가 아직 없습니다."}
        </div>
      ) : (
        <ul className="space-y-3">
          {rows.map((r) => (
            <li
              key={r.id}
              className="rounded-lg border border-black/10 p-4 dark:border-white/15"
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <a
                    href={r.occurrence?.detail_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-semibold leading-snug underline-offset-2 hover:underline"
                  >
                    {r.canonical_title}
                  </a>
                  <div className="mt-0.5 text-xs text-black/55 dark:text-white/55">
                    {r.institution ?? "기관 미상"}
                    {" · "}
                    {r.published_date
                      ? formatDateDot(r.published_date)
                      : (r.published_year ?? "연도 미상")}
                    {r.occurrence?.source && ` · ${r.occurrence.source.name}`}
                  </div>
                </div>
                <AccessButton row={r} />
              </div>

              {r.analysis && (
                <>
                  <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
                    {r.analysis.report_type && (
                      <span className="rounded bg-black/[0.06] px-1.5 py-0.5 dark:bg-white/10">
                        {r.analysis.report_type}
                      </span>
                    )}
                    {r.analysis.primary_robot_field && (
                      <span className="rounded bg-blue-50 px-1.5 py-0.5 text-blue-800 dark:bg-blue-500/15 dark:text-blue-300">
                        {r.analysis.primary_robot_field}
                      </span>
                    )}
                    {r.analysis.kiro_relevance === "직접" && (
                      <span className="rounded bg-amber-100 px-1.5 py-0.5 font-medium text-amber-900 dark:bg-amber-500/20 dark:text-amber-300">
                        KIRO 직접 관련
                      </span>
                    )}
                  </div>
                  {r.analysis.summary && (
                    <p className="mt-2 text-sm leading-relaxed text-black/75 dark:text-white/75">
                      {r.analysis.summary}
                    </p>
                  )}
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      <Pagination
        page={page}
        totalPages={totalPages}
        makeHref={(p) => `/reports${buildQuery(filters, p) || "?page=1"}`}
      />

      <p className="text-xs text-black/45 dark:text-white/45">
        요약은 각 보고서의 공개 메타데이터를 기반으로 AI가 작성했으며, 원문
        전체를 반영하지 않을 수 있습니다.
      </p>
    </div>
  );
}
