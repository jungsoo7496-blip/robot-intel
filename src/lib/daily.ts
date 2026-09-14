import "server-only";

import { unstable_cache } from "next/cache";

import { createServiceRoleClient } from "@/lib/supabase/server";
import type { PublishedItem } from "@/types/analysis";

/**
 * 일간 리포트 — 조간(朝刊) 모델 (사용자 지시 2026-08-08):
 * 날짜 D의 호(號) = 전날 08:00 ~ 당일 08:00 KST 사이 게시 동향 취합.
 * 아침 8시에 그날 호가 열리고, 8시 전에는 어제 호가 최신이다.
 * 겹침 없고 아카이브 명확 — 새 저장·이미지·Gemini 호출 없는 조립 뷰.
 * - 한국로봇융합연구원 언급 기사가 있으면 최상단 섹션, 없으면 자동 생략
 * - 분야별(정책·산업·기술) 목록 + 마감 임박 공고(D-day)
 */

const KST_OFFSET_MS = 9 * 60 * 60 * 1000; // KST는 서머타임 없음
const EDITION_HOUR = 8; // 조간 마감 시각 (KST 오전 8시)
const DAY_MS = 24 * 60 * 60 * 1000;

/** 오늘의 KST 날짜 문자열 (YYYY-MM-DD) — 마감 D-day 계산용. */
export function kstToday(): string {
  return new Date(Date.now() + KST_OFFSET_MS).toISOString().slice(0, 10);
}

/** 지금 시점의 최신 호 날짜: 아침 8시가 지나야 오늘 호가 열린다. */
export function currentEditionDate(): string {
  return new Date(Date.now() + KST_OFFSET_MS - EDITION_HOUR * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 10);
}

/** 호 날짜 D의 취합 창: [D-1 08:00, D 08:00) KST — UTC ISO 경계. */
export function editionWindow(date: string): [string, string] {
  const endMs = Date.parse(`${date}T0${EDITION_HOUR}:00:00+09:00`);
  return [new Date(endMs - DAY_MS).toISOString(), new Date(endMs).toISOString()];
}

export function shiftDate(date: string, days: number): string {
  const ms = Date.parse(`${date}T00:00:00Z`) + days * 24 * 60 * 60 * 1000;
  return new Date(ms).toISOString().slice(0, 10);
}

export type DailyReport = {
  date: string; // 호 날짜 (KST YYYY-MM-DD) — 창은 전날 08:00 ~ 당일 08:00
  rangeStart: string; // = date - 1 (창 시작일, 표기용)
  /** 창 내 실제 게시 건수 (DB count — 표시 항목이 잘려도 이 값은 정확) */
  total: number;
  /** 실제로 가져와 화면에 쓰는 건수 (total > fetched면 절단 표시) */
  fetched: number;
  /** "한국로봇융합연구원"이 언급된 기사 (사용자 정의) — 없으면 빈 배열(섹션 생략) */
  kiroMention: PublishedItem[];
  byCategory: Record<"정책", PublishedItem[]> &
    Record<"산업", PublishedItem[]> &
    Record<"기술", PublishedItem[]>;
  prevDate: string;
  nextDate: string | null; // 최신호면 null
};
// 마감 임박 공고는 열람일 기준이라 호 캐시에서 분리 — 페이지가
// getRobotAnnouncements(8)을 따로 부른다 (rnd.ts).

const KIRO_TOKEN = "로봇융합연구원"; // "한국로봇융합연구원" 포함

/**
 * 기관명 언급 판정 (게시물 자체 필드): 제목·한 줄 요약 기준.
 * 사용자 정의(2026-08-07): 본문에 스치듯 언급된 기사(관련성만 높은 것)는
 * 제외 — 기관명이 제목/요약 표면에 드러난 기사만 "우리 연구원 소식"이다.
 * AI 분석 필드(시사점 등)는 어떤 기사든 KIRO를 언급하므로 판정에 쓰지 않는다.
 */
export function mentionsKiro(item: PublishedItem): boolean {
  return (
    item.title.includes(KIRO_TOKEN) ||
    (item.one_line_summary ?? "").includes(KIRO_TOKEN)
  );
}

/**
 * 클러스터 구성원 기준 언급 판정: 묶인 원문 기사 중 하나라도 **제목에**
 * 기관명이 있으면 언급으로 본다 — 중복 병합으로 기관명 제목 기사가
 * 대표에서 밀려나도 섹션에서 빠지지 않게 한다. (전용 쿼리 수집 여부나
 * 피드 요약은 본문 언급만 있는 기사까지 통과시켜 기준에서 제외)
 */
async function clusterIdsMentioningKiro(
  clusterIds: string[],
): Promise<Set<string>> {
  if (clusterIds.length === 0) return new Set();
  // 병합으로 숨겨진 클러스터의 구성원도 봐야 하므로 서비스 롤로 읽는다
  // (읽기 전용 집계 — 결과는 게시물 표시 여부 판정에만 쓰인다)
  const supabase = createServiceRoleClient();

  // 이 클러스터들로 병합된(숨겨진) 클러스터 → 최종 대표 매핑.
  // 병합이 사슬(A→B→C)로 이어질 수 있어 고정점까지 반복 추적한다 (최대 4단).
  const childToParent = new Map<string, string>();
  let frontier = clusterIds;
  for (let depth = 0; depth < 4 && frontier.length > 0; depth++) {
    const { data: merged } = await supabase
      .from("content_clusters")
      .select("id, merged_into_cluster_id")
      .in("merged_into_cluster_id", frontier);
    const next: string[] = [];
    for (const row of merged ?? []) {
      if (!row.merged_into_cluster_id || childToParent.has(row.id)) continue;
      // 부모가 이미 다른 대표로 매핑돼 있으면 최종 대표로 승격
      const root =
        childToParent.get(row.merged_into_cluster_id) ??
        row.merged_into_cluster_id;
      childToParent.set(row.id, root);
      next.push(row.id);
    }
    frontier = next;
  }

  const allIds = [...clusterIds, ...childToParent.keys()];
  const hit = new Set<string>();

  // (a) 구성 기사 제목에 기관명
  const { data } = await supabase
    .from("cluster_members")
    .select("cluster_id, raw_items!inner(title)")
    .in("cluster_id", allIds);
  for (const row of (data ?? []) as unknown as Array<{
    cluster_id: string;
    raw_items: { title: string | null } | null;
  }>) {
    if ((row.raw_items?.title ?? "").includes(KIRO_TOKEN)) {
      hit.add(childToParent.get(row.cluster_id) ?? row.cluster_id);
    }
  }

  // (b) 병합으로 숨겨진 게시물의 제목·한줄요약에 기관명 —
  // 중복 정리에서 기관명 없는 변형이 대표가 돼도 언급이 유실되지 않게
  const { data: pubs } = await supabase
    .from("published_items")
    .select("cluster_id, title, one_line_summary")
    .in("cluster_id", allIds);
  for (const p of pubs ?? []) {
    if (
      (p.title ?? "").includes(KIRO_TOKEN) ||
      (p.one_line_summary ?? "").includes(KIRO_TOKEN)
    ) {
      hit.add(childToParent.get(p.cluster_id) ?? p.cluster_id);
    }
  }
  return hit;
}

const CATEGORY_KEYS = ["정책", "산업", "기술"] as const;

/** 섹션 내 정렬: 중요도 → KIRO 관련성 → 최신. */
function sectionSort(a: PublishedItem, b: PublishedItem) {
  const imp: Record<string, number> = { 높음: 0, 보통: 1, 낮음: 2 };
  const rel: Record<string, number> = { 직접: 0, 간접: 1 };
  return (
    (imp[a.importance] ?? 3) - (imp[b.importance] ?? 3) ||
    (rel[a.kiro_relevance] ?? 2) - (rel[b.kiro_relevance] ?? 2) ||
    new Date(b.display_date).getTime() - new Date(a.display_date).getTime()
  );
}

async function buildDailyReport(date: string): Promise<DailyReport> {
  // 쿠키 비의존(서비스 롤) — unstable_cache 안에서 실행 가능한 공개 읽기
  const supabase = createServiceRoleClient();
  const rangeStart = shiftDate(date, -1);
  const [dayStart, dayEnd] = editionWindow(date);

  // 외부 리뷰 P1-2: limit로 조용히 잘리지 않게 실제 건수는 count로 별도 확보.
  // 현 규모(호당 ~250건) 대비 1000이면 사실상 전량 — 넘으면 화면에 명시된다.
  const FETCH_LIMIT = 1000;
  const { data, count, error } = await supabase
    .from("published_items")
    .select("*", { count: "exact" })
    .eq("is_visible", true)
    .gte("display_date", dayStart)
    .lt("display_date", dayEnd)
    .order("display_date", { ascending: false })
    .limit(FETCH_LIMIT);

  // 2026-08-20 실사고: 조회가 순간 실패하면 supabase-js는 예외 없이
  // data=null을 돌려주고, 그 "0건" 리포트가 unstable_cache에 저장돼
  // 이후 방문자 전원이 빈 오늘 호를 보게 된다 (DB에는 349건이 있었다).
  // 실패는 던져서 캐시에 저장되지 않게 한다 — Next가 이전 정상 캐시를
  // 대신 서빙한다(stale-if-error).
  if (error) {
    throw new Error(`일간 리포트 조회 실패: ${error.message}`);
  }

  const items = ((data ?? []) as PublishedItem[]).toSorted(sectionSort);

  const byCategory = { 정책: [], 산업: [], 기술: [] } as DailyReport["byCategory"];
  for (const item of items) {
    const key = CATEGORY_KEYS.find((c) => c === item.category);
    if (key) byCategory[key].push(item);
  }

  const mentionClusters = await clusterIdsMentioningKiro(
    items.map((i) => i.cluster_id),
  );

  return {
    date,
    rangeStart,
    total: count ?? items.length,
    fetched: items.length,
    kiroMention: items.filter(
      (i) => mentionClusters.has(i.cluster_id) || mentionsKiro(i),
    ),
    byCategory,
    prevDate: shiftDate(date, -1),
    nextDate: date < currentEditionDate() ? shiftDate(date, 1) : null,
  };
}

// 호 캐싱 (사용자 지적 2026-08-08 — "매번 새로 생성하는 느낌"):
// 지난 호는 창(전일 08시~당일 08시)이 닫혀 내용이 사실상 확정 —
// 하루 캐시. 오늘 호는 분석이 계속 채워지므로 5분 캐시.
// (지난 호도 소급 분석 반영을 위해 무기한이 아닌 하루로 둔다)
const getPastEdition = unstable_cache(buildDailyReport, ["daily-edition-past-v2"], {
  revalidate: 60 * 60 * 24,
});
const getTodayEdition = unstable_cache(
  buildDailyReport,
  ["daily-edition-today-v3"],  // v3: 2026-09-01 정당한-0건 박제 사고로 재교체
  { revalidate: 60 * 5 },
);

export async function getDailyReport(date: string): Promise<DailyReport> {
  const isPast = date < currentEditionDate();
  let report = isPast ? await getPastEdition(date) : await getTodayEdition(date);

  // "오늘 호 0건" 캐시는 신뢰하지 않는다 (2026-09-01 실사고 — 두 번째).
  // 아침에 잠시 비어 있던 순간이 캐시에 저장되면, 이후 기사가 143건
  // 차올라도 화면은 계속 0건을 보여줬다 (5분 revalidate가 갈아끼우지
  // 못하고 눌러앉는 것을 8/20에 이어 재관측). 빈 오늘 호는 드문 상태라
  // 캐시 이득도 없다 — 실시간 조회로 우회한다. 지난 호의 0건은 확정
  // 상태이므로 그대로 둔다.
  if (!isPast && report.fetched === 0) {
    report = await buildDailyReport(date);
  }

  // nextDate는 열람 시점에 따라 달라지므로 캐시 값 위에서 재계산
  return {
    ...report,
    nextDate: isPast ? shiftDate(date, 1) : null,
  };
}
