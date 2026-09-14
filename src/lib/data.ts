import "server-only";

import { createClient, createServiceRoleClient } from "@/lib/supabase/server";
import type { PublishedItem } from "@/types/analysis";

export const PAGE_SIZE = 20;

export type ItemFilters = {
  category?: string;
  region?: string;
  robot_field?: string;
  importance?: string;
  evidence_level?: string;
  kiro_axis?: string; // KIRO 업무축 (v1.1 — analyses.kiro_relevance_axes)
  days?: number; // 최근 N일
  month?: string; // YYYY-MM (아카이브 탐색)
  from?: string; // display_date 범위 시작 (ISO — 일간 리포트 연계)
  to?: string; // display_date 범위 끝 (미만 비교)
  sort?: string; // latest(기본) | importance | kiro
  page?: number;
};

// 서비스/물류 분리(0012) — legacy 통합값도 함께 조회해 과거 데이터 연속성 유지
const ROBOT_FIELD_WITH_LEGACY: Record<string, string[]> = {
  "서비스 로봇": ["서비스 로봇", "서비스·물류 로봇"],
  "물류 로봇": ["물류 로봇", "서비스·물류 로봇"],
};

/** 최신 동향 목록 (FR-008). 필터는 AND 결합. */
export async function getItems(filters: ItemFilters) {
  const supabase = await createClient();
  const page = Math.max(1, filters.page ?? 1);

  // 업무축 필터는 현재 분석(analyses)과 inner join해 jsonb 배열 포함 검색
  // (GIN 인덱스 사용, v1.1 분석부터 값이 존재)
  // supabase-js 타입 파서가 동적 embed 문자열을 해석하지 못해 명시적으로 우회
  const select = filters.kiro_axis
    ? "*, kiro:analyses!published_items_current_analysis_id_fkey!inner(kiro_relevance_axes)"
    : "*";

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let query: any = supabase
    .from("published_items")
    .select(select as "*", { count: "exact" })
    .eq("is_visible", true)
    .range((page - 1) * PAGE_SIZE, page * PAGE_SIZE - 1);

  // 정렬 (사용자 피드백): 최신순 / 중요도순 / KIRO 관련도순
  // "최신"은 게시 시각이 아니라 원문 발행일(display_date) 기준 — 배치가
  // 키워드 뭉치 단위로 게시해도 특정 수집원이 상단을 점령하지 않는다 (0014)
  if (filters.sort === "importance") {
    query = query
      .order("importance_rank", { ascending: true })
      .order("display_date", { ascending: false });
  } else if (filters.sort === "kiro") {
    query = query
      .order("kiro_relevance_rank", { ascending: true })
      .order("display_date", { ascending: false });
  } else {
    query = query
      .order("display_date", { ascending: false })
      .order("published_at", { ascending: false });
  }

  if (filters.kiro_axis) {
    query = query.filter(
      "kiro.kiro_relevance_axes",
      "cs",
      JSON.stringify([filters.kiro_axis]),
    );
  }
  if (filters.category) query = query.eq("category", filters.category);
  if (filters.region) query = query.eq("region", filters.region);
  if (filters.robot_field) {
    const withLegacy = ROBOT_FIELD_WITH_LEGACY[filters.robot_field];
    query = withLegacy
      ? query.in("robot_field", withLegacy)
      : query.eq("robot_field", filters.robot_field);
  }
  if (filters.importance) query = query.eq("importance", filters.importance);
  if (filters.evidence_level)
    query = query.eq("evidence_level", filters.evidence_level);
  if (filters.days) {
    const since = new Date();
    since.setDate(since.getDate() - filters.days);
    query = query.gte("display_date", since.toISOString());
  }
  if (filters.month && /^\d{4}-\d{2}$/.test(filters.month)) {
    const [y, m] = filters.month.split("-").map(Number);
    const start = new Date(Date.UTC(y, m - 1, 1));
    const end = new Date(Date.UTC(y, m, 1));
    query = query
      .gte("display_date", start.toISOString())
      .lt("display_date", end.toISOString());
  }
  // 일간 리포트 "나머지 N건" 연계 — 그 호의 24시간 창 유지 (외부 리뷰 P2-2)
  if (filters.from) query = query.gte("display_date", filters.from);
  if (filters.to) query = query.lt("display_date", filters.to);

  const { data, count, error } = await query;
  if (error) throw error;
  return { items: (data ?? []) as PublishedItem[], total: count ?? 0, page };
}

/** 동향 상세: 게시물 + 현재 분석 + 정책 상세 (FR-009). */
export async function getItemDetail(id: string) {
  const supabase = await createClient();
  const { data: item } = await supabase
    .from("published_items")
    .select("*")
    .eq("id", id)
    .maybeSingle();
  if (!item) return null;

  const [{ data: analysis }, { data: policy }] = await Promise.all([
    supabase
      .from("analyses")
      .select("*")
      .eq("id", item.current_analysis_id)
      .maybeSingle(),
    supabase
      .from("policy_details")
      .select("*")
      .eq("published_item_id", id)
      .maybeSingle(),
  ]);

  return { item, analysis, policy };
}

/**
 * 관련 출처 목록 (FR-009).
 * raw_items는 RLS상 운영자 전용이므로 서버에서 service role로
 * 제목·URL·출처명만 제한적으로 조회해 넘긴다.
 */
export async function getRelatedSources(clusterId: string) {
  const supabase = createServiceRoleClient();
  const { data } = await supabase
    .from("cluster_members")
    .select(
      "is_representative, raw_items(title, url, published_at, sources(name))",
    )
    .eq("cluster_id", clusterId)
    .order("is_representative", { ascending: false });

  type Row = {
    is_representative: boolean;
    raw_items: {
      title: string | null;
      url: string;
      published_at: string | null;
      sources: { name: string } | null;
    } | null;
  };
  return ((data ?? []) as unknown as Row[])
    .filter((r) => r.raw_items)
    .map((r) => ({
      isRepresentative: r.is_representative,
      title: r.raw_items!.title ?? "(제목 없음)",
      url: r.raw_items!.url,
      publishedAt: r.raw_items!.published_at,
      sourceName: r.raw_items!.sources?.name ?? "수동 등록",
    }));
}

/** 한국어 부분 문자열 검색 (FR-011). 2글자 이상만 부분 일치. */
export async function searchItems(q: string, page = 1) {
  const supabase = await createClient();
  const keyword = q.trim();
  if (keyword.length < 2) {
    return { items: [] as PublishedItem[], total: 0, page: 1, tooShort: true };
  }

  const escaped = keyword.replace(/[%_]/g, (m) => `\\${m}`);

  // 제목 일치 우선을 전체 결과 기준으로 (외부 리뷰 P2-1) — 정렬은 DB의
  // search_published_items(0018)가 수행한 뒤 페이지를 자른다.
  const [{ data, error }, { count, error: countError }] = await Promise.all([
    supabase.rpc("search_published_items", {
      q: escaped,
      page_limit: PAGE_SIZE,
      page_offset: (page - 1) * PAGE_SIZE,
    }),
    supabase
      .from("published_items")
      .select("id", { count: "exact", head: true })
      .eq("is_visible", true)
      .ilike("search_text", `%${escaped}%`),
  ]);
  if (error) throw error;
  if (countError) throw countError;

  return {
    items: (data ?? []) as PublishedItem[],
    total: count ?? 0,
    page,
    tooShort: false,
  };
}

export type PolicyRow = {
  published_item_id: string;
  policy_name: string | null;
  project_name: string | null;
  ministries: string[] | null;
  organizations: string[] | null;
  budget_text: string | null;
  budget_amount_krw: number | null;
  project_start_date: string | null;
  project_end_date: string | null;
  support_targets: string[] | null;
  announcement_status: string | null;
  application_deadline: string | null;
  target_region: string | null;
  published_items: {
    id: string;
    title: string;
    robot_field: string;
    region: string;
    source_published_at: string | null;
    published_at: string;
    is_visible: boolean;
  } | null;
};

/** 정책·R&D 목록 (FR-010). */
export async function getPolicies(filters: {
  ministry?: string;
  region?: string;
  robot_field?: string;
  status?: string;
  page?: number;
}) {
  const supabase = await createClient();
  const page = Math.max(1, filters.page ?? 1);

  let query = supabase
    .from("policy_details")
    .select("*, published_items!inner(*)", { count: "exact" })
    .eq("published_items.is_visible", true)
    // 의미 있는 정책 행만: 부처·예산·기간 중 하나는 확인된 것 (사용자 피드백)
    .or("ministries.not.is.null,budget_text.not.is.null,project_start_date.not.is.null")
    // 원문 발행일 기준 정렬 (0014) — 등록 배치 순서가 아니라 기사 날짜순
    .order("published_items(display_date)", { ascending: false })
    .range((page - 1) * PAGE_SIZE, page * PAGE_SIZE - 1);

  if (filters.ministry) query = query.contains("ministries", [filters.ministry]);
  if (filters.status) query = query.eq("announcement_status", filters.status);
  if (filters.region)
    query = query.eq("published_items.region", filters.region);
  if (filters.robot_field)
    query = query.eq("published_items.robot_field", filters.robot_field);

  const { data, count, error } = await query;
  if (error) throw error;
  return { rows: (data ?? []) as unknown as PolicyRow[], total: count ?? 0, page };
}

/** 홈 화면 데이터 (tasks §10.3).

분야별 목록은 공용 최신 풀이 아니라 **분야마다 독립 조회**한다 —
한 분야의 수집량이 폭증해도 다른 분야의 중요 항목이 밀리지 않는다.
*/
export async function getHomeData() {
  const supabase = await createClient();

  const categoryQuery = (category: string) =>
    supabase
      .from("published_items")
      .select("*")
      .eq("is_visible", true)
      .eq("category", category)
      .order("display_date", { ascending: false })
      .limit(40);

  const [important, policy, industry, tech, latestBrief] = await Promise.all([
    // 주요 동향: 분야별 열(중요도 기준)과 차별화 — KIRO 관련성 기준으로
    // 선별하므로 최신 풀만 넓게 가져오고 선정은 화면에서 (사용자 피드백)
    supabase
      .from("published_items")
      .select("*")
      .eq("is_visible", true)
      .order("display_date", { ascending: false })
      .limit(60),
    categoryQuery("정책"),
    categoryQuery("산업"),
    categoryQuery("기술"),
    supabase
      .from("briefs")
      .select("id, title, published_at")
      .eq("status", "PUBLISHED")
      .order("published_at", { ascending: false })
      .limit(1)
      .maybeSingle(),
  ]);

  return {
    important: (important.data ?? []) as PublishedItem[],
    policy: (policy.data ?? []) as PublishedItem[],
    industry: (industry.data ?? []) as PublishedItem[],
    tech: (tech.data ?? []) as PublishedItem[],
    latestBrief: latestBrief.data,
  };
}
