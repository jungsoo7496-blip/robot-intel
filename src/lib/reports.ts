import "server-only";

import { createServiceRoleClient } from "@/lib/supabase/server";

/**
 * 정책·동향 보고서 조회 (보고서 스펙 §28~30).
 * 공개 조건은 배치가 is_visible로 미리 계산한다 — 여기서는 is_visible만 믿는다.
 * 읽기 전용 공개 데이터라 service role 사용은 rnd.ts와 같은 패턴.
 */

export type ReportAnalysis = {
  robot_relevance: "DIRECT" | "RELATED" | "WEAK" | "EXCLUDE";
  report_type: string | null;
  primary_robot_field: string | null;
  robot_fields: string[] | null;
  summary: string | null;
  kiro_relevance: string | null;
  kiro_relevance_axes: string[] | null;
  kiro_reason: string | null;
  keywords: string[] | null;
  limitations: string | null;
};

export type ReportOccurrence = {
  access_status:
    | "DIRECT_DOWNLOAD"
    | "SOURCE_DOWNLOAD"
    | "VIEW_ONLY"
    | "METADATA_ONLY"
    | "BLOCKED"
    | "UNKNOWN";
  detail_url: string;
  candidate_download_url: string | null;
  file_format: string | null;
  source: { source_key: string; name: string } | null;
};

export type ReportRow = {
  id: string;
  canonical_title: string;
  institution: string | null;
  published_date: string | null;
  published_year: number | null;
  analysis: ReportAnalysis | null;
  occurrence: ReportOccurrence | null;
};

export type ReportFilters = {
  q?: string;
  type?: string; // report_type
  field?: string; // robot_field (primary 또는 robot_fields 포함)
  access?: string; // access_status
  source?: string; // source_key
  year?: string;
};

/** 필터 셀렉트 옵션 (스펙 §21·§23). */
export const REPORT_TYPES = [
  "정책·전략",
  "산업·시장",
  "기술·R&D",
  "통계·실태조사",
  "법·제도·규제",
  "로드맵·계획",
  "사업·성과·평가",
  "동향·브리프",
  "학술·연구",
  "기타",
] as const;

export const REPORT_ROBOT_FIELDS = [
  "휴머노이드·피지컬 AI",
  "제조·산업용 로봇",
  "서비스 로봇",
  "물류 로봇",
  "의료·돌봄 로봇",
  "농업 로봇",
  "국방·재난 로봇",
  "해양·특수환경 로봇",
  "핵심 부품·소프트웨어",
  "기타",
] as const;

export const ACCESS_LABELS: Record<string, string> = {
  DIRECT_DOWNLOAD: "원문 다운로드",
  SOURCE_DOWNLOAD: "공식페이지 다운로드",
  VIEW_ONLY: "원문 열람",
};

/**
 * 필터가 걸린 임베드는 !inner로 만들어 서버에서 부모 행까지 걸러낸다 —
 * 아니면 count·페이지네이션이 자식 null 행을 포함해 어긋난다.
 */
function buildSelect(filters: ReportFilters): string {
  const analysisInner = filters.type || filters.field ? "!inner" : "";
  const occInner = filters.access || filters.source ? "!inner" : "";
  const sourceInner = filters.source ? "!inner" : "";
  return `
  id, canonical_title, institution, published_date, published_year,
  analysis:report_analyses!report_documents_current_analysis_fk${analysisInner} (
    robot_relevance, report_type, primary_robot_field, robot_fields,
    summary, kiro_relevance, kiro_relevance_axes, kiro_reason,
    keywords, limitations
  ),
  occurrence:report_occurrences!report_documents_preferred_occurrence_id_fkey${occInner} (
    access_status, detail_url, candidate_download_url, file_format,
    source:report_sources!report_occurrences_source_id_fkey${sourceInner} ( source_key, name )
  )
`;
}

export async function getPublishedReports(
  filters: ReportFilters,
  page = 1,
  pageSize = 20,
) {
  const supabase = createServiceRoleClient();

  let query = supabase
    .from("report_documents")
    .select(buildSelect(filters), { count: "exact" })
    .eq("is_visible", true);

  if (filters.q) {
    const safe = filters.q.replace(/[%_,()]/g, " ").trim();
    if (safe) {
      query = query.or(
        `canonical_title.ilike.%${safe}%,institution.ilike.%${safe}%`,
      );
    }
  }
  if (filters.year && /^\d{4}$/.test(filters.year)) {
    query = query.eq("published_year", Number(filters.year));
  }
  // 임베드 필터는 select의 alias 경로를 쓴다. 매칭 안 되는 부모 행은
  // 자식이 null로 남을 수 있어 아래 post-filter가 최종 확정한다.
  if (filters.type) {
    query = query.eq("analysis.report_type" as never, filters.type);
  }
  if (filters.field) {
    query = query.contains(
      "analysis.robot_fields" as never,
      JSON.stringify([filters.field]),
    );
  }
  if (filters.access) {
    query = query.eq("occurrence.access_status" as never, filters.access);
  }
  if (filters.source) {
    query = query.eq("occurrence.source.source_key" as never, filters.source);
  }

  const { data, count, error } = await query
    .order("published_date", { ascending: false, nullsFirst: false })
    .order("published_year", { ascending: false, nullsFirst: false })
    .order("created_at", { ascending: false })
    .range((page - 1) * pageSize, page * pageSize - 1);
  if (error) throw error;

  // 임베드 필터는 매칭 실패 시 자식을 null로 두므로 (inner join이 아님)
  // 자식 조건이 있는 필터는 여기서 최종 확정한다.
  let rows = (data ?? []) as unknown as ReportRow[];
  if (filters.type) {
    rows = rows.filter((r) => r.analysis?.report_type === filters.type);
  }
  if (filters.field) {
    rows = rows.filter((r) =>
      r.analysis?.robot_fields?.includes(filters.field as string),
    );
  }
  if (filters.access) {
    rows = rows.filter((r) => r.occurrence?.access_status === filters.access);
  }
  if (filters.source) {
    rows = rows.filter(
      (r) => r.occurrence?.source?.source_key === filters.source,
    );
  }

  return { rows, total: count ?? 0, page, pageSize };
}

/** 필터 드롭다운용 — 공개 보고서가 있는 출처·연도 목록. */
export async function getReportFilterOptions() {
  const supabase = createServiceRoleClient();
  const [sources, years] = await Promise.all([
    supabase
      .from("report_sources")
      .select("source_key, name")
      .in("status", ["ACTIVE", "PAUSED"])
      .order("priority"),
    supabase
      .from("report_documents")
      .select("published_year")
      .eq("is_visible", true)
      .not("published_year", "is", null)
      .order("published_year", { ascending: false })
      .limit(1000),
  ]);
  const yearSet = new Set<number>();
  for (const r of years.data ?? []) {
    if (r.published_year) yearSet.add(r.published_year);
  }
  return {
    sources: sources.data ?? [],
    years: [...yearSet].sort((a, b) => b - a),
  };
}
