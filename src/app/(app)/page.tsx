import Link from "next/link";

import { CategoryBadge, formatDate } from "@/components/item-card";
import { getHomeData } from "@/lib/data";
import type { PublishedItem } from "@/types/analysis";

export const dynamic = "force-dynamic";

/** 홈 전용 압축 카드 — 한 화면 안에 들어오도록 요약만 짧게. */
function CompactCard({ item }: { item: PublishedItem }) {
  return (
    <Link
      href={`/trends/${item.id}`}
      className="block rounded-xl border border-black/10 bg-white p-4 transition hover:border-black/25 hover:shadow-sm dark:border-white/12 dark:bg-white/[0.03] dark:hover:border-white/35"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <CategoryBadge category={item.category} />
        <span className="text-xs text-black/45 dark:text-white/45">
          {item.region} · {formatDate(item.source_published_at ?? item.published_at)}
        </span>
      </div>
      <h3 className="mt-1.5 font-bold leading-snug">{item.title}</h3>
      {item.one_line_summary && (
        <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-black/70 dark:text-white/70">
          {item.one_line_summary}
        </p>
      )}
    </Link>
  );
}

/**
 * 홈 정책 박스는 국가정책 수준 중심으로 보여준다 (UI/UX 개선 7).
 * 제목 앞머리가 지자체 행위자면 제외하되, 경상북도(경북)만 예외로 허용.
 */
const LOCAL_ACTOR_RE =
  /^(서울|부산|인천|광주|대전|울산|세종|경기|강원|충청|충북|충남|전라|전북|전남|경남|제주|대구(?!경북))|^[가-힣]{1,8}(특별시|광역시|특별자치시|특별자치도|시|군|구|도)(청)?[,·\s]/;

function isNationalLevelPolicy(title: string): boolean {
  if (/^(경상북도|경북)/.test(title)) return true;
  return !LOCAL_ACTOR_RE.test(title);
}

const IMPORTANCE_RANK: Record<string, number> = { 높음: 0, 보통: 1, 낮음: 2 };

function byImportanceThenDate(a: PublishedItem, b: PublishedItem) {
  const rankDiff =
    (IMPORTANCE_RANK[a.importance] ?? 3) - (IMPORTANCE_RANK[b.importance] ?? 3);
  if (rankDiff !== 0) return rankDiff;
  return (
    new Date(b.display_date).getTime() - new Date(a.display_date).getTime()
  );
}

// 관련성 순위는 DB 생성 컬럼(kiro_relevance_rank)과 동일 규칙 (0012)
const KIRO_RELEVANCE_RANK: Record<string, number> = { 직접: 0, 간접: 1 };

/** 주요 동향용: KIRO 관련성(직접→간접→그 외) → 중요도 → 최신 순. */
function byKiroRelevanceThenImportance(a: PublishedItem, b: PublishedItem) {
  const relDiff =
    (KIRO_RELEVANCE_RANK[a.kiro_relevance] ?? 2) -
    (KIRO_RELEVANCE_RANK[b.kiro_relevance] ?? 2);
  if (relDiff !== 0) return relDiff;
  return byImportanceThenDate(a, b);
}

/**
 * "최근 1일 이내" 기사를 주어진 기준으로 먼저 채우고, 그 창에 기사가
 * 모자랄 때만 그 이전 것으로 보충한다 (사용자 피드백 2026-08-07) —
 * 홈은 "지금 벌어지는 일" 우선, 단 빈칸은 만들지 않는다.
 */
function pickForHome(
  items: PublishedItem[],
  count: number,
  sorter: (a: PublishedItem, b: PublishedItem) => number,
) {
  const cutoff = Date.now() - 24 * 60 * 60 * 1000;
  const fresh: PublishedItem[] = [];
  const older: PublishedItem[] = [];
  for (const item of items) {
    (new Date(item.display_date).getTime() >= cutoff ? fresh : older).push(
      item,
    );
  }
  return [...fresh.toSorted(sorter), ...older.toSorted(sorter)].slice(0, count);
}

/**
 * 홈 (tasks §10.3, 사용자 피드백):
 * 분야별 최신을 상단에, 주요 동향은 그리드로 한눈에 — 한 페이지 구성.
 */
export default async function HomePage() {
  const { important, policy, industry, tech, latestBrief } =
    await getHomeData();

  // 분야별 열: "최근 1일 → 중요도 순" 상위 5개
  const byCategory = {
    정책: pickForHome(
      policy.filter((i) => isNationalLevelPolicy(i.title)),
      5,
      byImportanceThenDate,
    ),
    산업: pickForHome(industry, 5, byImportanceThenDate),
    기술: pickForHome(tech, 5, byImportanceThenDate),
  };
  // 주요 동향: "최근 1일 → KIRO 관련성 순" 8개 — 분야별 열과 기준을 분리
  const highlight = pickForHome(important, 8, byKiroRelevanceThenImportance);
  const hasAny = policy.length + industry.length + tech.length > 0;

  return (
    <div className="space-y-7">
      {latestBrief && (
        <Link
          href={`/briefs/${latestBrief.id}`}
          className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-blue-200 bg-blue-50/60 px-4 py-3 transition hover:border-blue-400 dark:border-blue-900 dark:bg-blue-950/30 dark:hover:border-blue-700"
        >
          <div>
            <span className="mr-2 rounded bg-blue-600 px-1.5 py-0.5 text-xs font-bold text-white">
              브리프
            </span>
            <span className="font-semibold">{latestBrief.title}</span>
          </div>
          <span className="text-sm text-black/50 dark:text-white/50">
            {formatDate(latestBrief.published_at)} 발행 →
          </span>
        </Link>
      )}

      {/* items-start: 박스 높이를 내용에 맞춤 — 강제 동일 높이 제거 (UI/UX 6) */}
      <section className="grid items-start gap-5 md:grid-cols-3">
        {(Object.entries(byCategory) as [string, PublishedItem[]][]).map(
          ([category, items]) => (
            <div
              key={category}
              className="rounded-xl border border-black/10 p-4 dark:border-white/12"
            >
              <h2 className="flex items-baseline justify-between border-b border-black/10 pb-2 font-bold dark:border-white/12">
                {category}
                <Link
                  href={`/trends?category=${category}`}
                  className="text-xs font-normal text-black/50 underline dark:text-white/50"
                >
                  전체 보기
                </Link>
              </h2>
              {items.length === 0 ? (
                <p className="mt-3 text-sm text-black/40 dark:text-white/40">
                  아직 없음
                </p>
              ) : (
                <ul className="mt-2 divide-y divide-black/5 dark:divide-white/8">
                  {items.map((item) => (
                    <li key={item.id} className="py-2">
                      <Link
                        href={`/trends/${item.id}`}
                        className="block text-sm font-medium leading-snug underline-offset-2 hover:underline"
                      >
                        {item.title}
                      </Link>
                      <div className="mt-0.5 text-xs text-black/40 dark:text-white/40">
                        {item.region} ·{" "}
                        {formatDate(item.source_published_at ?? item.published_at)}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ),
        )}
      </section>

      {highlight.length > 0 && (
        <section>
          <h2 className="mb-3 flex items-baseline justify-between text-lg font-bold">
            주요 동향
            <Link
              href="/trends?sort=kiro"
              className="text-xs font-normal text-black/50 underline dark:text-white/50"
            >
              전체 보기
            </Link>
          </h2>
          <div className="grid items-start gap-3 sm:grid-cols-2">
            {highlight.map((item) => (
              <CompactCard key={item.id} item={item} />
            ))}
          </div>
        </section>
      )}

      {!hasAny && (
        <section className="rounded-xl border border-dashed border-black/20 p-10 text-center text-sm text-black/50 dark:border-white/25 dark:text-white/50">
          아직 게시된 동향이 없습니다. 수집·분석 배치가 자동 실행되면 이곳에
          표시됩니다.
        </section>
      )}
    </div>
  );
}
