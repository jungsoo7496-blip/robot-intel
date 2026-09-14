import Link from "next/link";

import {
  CategoryBadge,
  ItemCard,
  formatDateDot,
} from "@/components/item-card";
import { currentEditionDate, editionWindow, getDailyReport } from "@/lib/daily";
import { daysLeft, getImminentAnnouncements } from "@/lib/rnd";
import type { PublishedItem } from "@/types/analysis";

export const dynamic = "force-dynamic";

type SearchParams = { [key: string]: string | undefined };

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

function dateLabel(date: string) {
  // KST 자정의 UTC 요일 +1 = KST 요일 (KST는 UTC+9 고정)
  const d = new Date(`${date}T00:00:00+09:00`);
  const [y, m, day] = date.split("-");
  return `${y}.${m}.${day}.(${WEEKDAYS[(d.getUTCDay() + 1) % 7]})`;
}

/** 항목 옆 짧은 날짜 (8.7) — 이틀 종합 창에서 어제/오늘 구분용. */
function shortDate(value: string) {
  const d = new Date(new Date(value).getTime() + 9 * 60 * 60 * 1000);
  return `${d.getUTCMonth() + 1}.${d.getUTCDate()}`;
}

/** 밀도 높은 한 줄 항목 (일간 리포트 전용 — 이미지 없음). */
function CompactRow({ item }: { item: PublishedItem }) {
  return (
    <li className="py-2.5 first:pt-0 last:pb-0">
      <Link href={`/trends/${item.id}`} className="group block">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="font-semibold leading-snug group-hover:underline">
            {item.title}
          </span>
          <span className="text-xs text-black/45 dark:text-white/45">
            {shortDate(item.display_date)} ·{" "}
            {item.representative_source_name ?? "출처 미상"}
            {item.importance === "높음" && " · 중요"}
            {item.kiro_relevance === "직접" && " · KIRO 직접"}
          </span>
        </div>
        {item.one_line_summary && (
          <p className="mt-0.5 line-clamp-1 text-sm leading-relaxed text-black/65 dark:text-white/65">
            {item.one_line_summary}
          </p>
        )}
      </Link>
    </li>
  );
}

/** 일간 리포트 (조간 모델): 전날 08시~당일 08시 취합분을 보고서로 조립. */
export default async function DailyPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await searchParams;
  // 아침 8시에 오늘 호가 열린다 — 그 전에는 어제 호가 최신
  const latest = currentEditionDate();
  const date =
    params.date && /^\d{4}-\d{2}-\d{2}$/.test(params.date) && params.date <= latest
      ? params.date
      : latest;

  // 호 내용은 캐시(지난 호 하루·오늘 호 5분), 마감 공고는 열람일 기준이라 분리
  // 임박 = D-7 이내만 (같은 공고가 매일 반복되지 않게 — 사용자 지시)
  const [report, deadlines] = await Promise.all([
    getDailyReport(date),
    getImminentAnnouncements(7, 8),
  ]);
  // "나머지 N건" 링크가 이 호의 24시간 창을 유지하도록 (외부 리뷰 P2-2)
  const [windowFrom, windowTo] = editionWindow(date);

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header className="border-b border-black/10 pb-4 dark:border-white/15">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h1 className="text-2xl font-bold">로봇 일간 리포트</h1>
          <nav className="flex items-center gap-2 text-sm">
            <Link
              href={`/daily?date=${report.prevDate}`}
              className="rounded-lg border border-black/15 px-2.5 py-1 hover:border-black/35 dark:border-white/20 dark:hover:border-white/45"
            >
              ← 전일
            </Link>
            {report.nextDate ? (
              <Link
                href={`/daily?date=${report.nextDate}`}
                className="rounded-lg border border-black/15 px-2.5 py-1 hover:border-black/35 dark:border-white/20 dark:hover:border-white/45"
              >
                익일 →
              </Link>
            ) : (
              <span className="rounded-lg border border-black/10 px-2.5 py-1 text-black/30 dark:border-white/10 dark:text-white/30">
                익일 →
              </span>
            )}
            {date !== latest && (
              <Link
                href="/daily"
                className="rounded-lg border border-black/15 px-2.5 py-1 hover:border-black/35 dark:border-white/20 dark:hover:border-white/45"
              >
                최신호
              </Link>
            )}
            {/* 특정 날짜 선택 (사용자 요청): GET 폼이라 JS 없이 동작 */}
            <form action="/daily" className="flex items-center gap-1">
              <input
                type="date"
                name="date"
                defaultValue={date}
                max={latest}
                className="rounded-lg border border-black/15 bg-transparent px-2 py-1 dark:border-white/20 dark:[color-scheme:dark]"
              />
              <button
                type="submit"
                className="rounded-lg border border-black/15 px-2.5 py-1 hover:border-black/35 dark:border-white/20 dark:hover:border-white/45"
              >
                이동
              </button>
            </form>
          </nav>
        </div>
        <p className="mt-1 text-sm text-black/60 dark:text-white/60">
          {dateLabel(report.date)} 조간 · {dateLabel(report.rangeStart)} 08:00
          ~ 당일 08:00 취합 · 게시 동향 {report.total}건
          {report.total > report.fetched &&
            ` (주요 ${report.fetched}건 기준 표시)`}{" "}
          · 수집·AI 분석 결과를 자동 조립한 리포트입니다
        </p>
      </header>

      {report.total === 0 ? (
        <div className="rounded-xl border border-dashed border-black/20 p-8 text-center text-sm text-black/50 dark:border-white/25 dark:text-white/50">
          이 날짜에 게시된 동향이 없습니다.
        </div>
      ) : (
        <>
          {report.kiroMention.length > 0 && (
            <section>
              <h2 className="mb-2 text-lg font-bold">
                🏛 우리 연구원 소식{" "}
                <span className="text-sm font-normal text-black/50 dark:text-white/50">
                  한국로봇융합연구원 언급 기사 {report.kiroMention.length}건
                </span>
              </h2>
              <div className="grid gap-2.5">
                {report.kiroMention.slice(0, 5).map((item) => (
                  <ItemCard key={item.id} item={item} />
                ))}
              </div>
            </section>
          )}

          {(["정책", "산업", "기술"] as const).map((cat) => {
            const rows = report.byCategory[cat];
            if (rows.length === 0) return null;
            return (
              <section key={cat}>
                <h2 className="mb-1 flex items-baseline gap-2 text-lg font-bold">
                  <CategoryBadge category={cat} />
                  <span>
                    {cat} 동향{" "}
                    <span className="text-sm font-normal text-black/50 dark:text-white/50">
                      {rows.length}건
                    </span>
                  </span>
                </h2>
                <ul className="divide-y divide-black/8 rounded-xl border border-black/10 bg-white px-4 py-3 dark:divide-white/10 dark:border-white/12 dark:bg-white/[0.03]">
                  {rows.slice(0, 12).map((item) => (
                    <CompactRow key={item.id} item={item} />
                  ))}
                </ul>
                {rows.length > 12 && (
                  <p className="mt-1 text-right text-xs text-black/45 dark:text-white/45">
                    <Link
                      href={`/trends?category=${cat}&from=${encodeURIComponent(windowFrom)}&to=${encodeURIComponent(windowTo)}`}
                      className="underline"
                    >
                      나머지 {rows.length - 12}건은 최신 동향에서 (이 호 기간)
                    </Link>
                  </p>
                )}
              </section>
            );
          })}
        </>
      )}

      {deadlines.rows.length > 0 && (
        <section>
          <h2 className="mb-2 text-lg font-bold">
            ⏳ 마감 임박 R&D 공고{" "}
            <span className="text-sm font-normal text-black/50 dark:text-white/50">
              NTIS · 오늘 기준
            </span>
          </h2>
          <ul className="divide-y divide-black/8 rounded-xl border border-black/10 bg-white px-4 py-3 dark:divide-white/10 dark:border-white/12 dark:bg-white/[0.03]">
            {deadlines.rows.map((a) => {
              const d = daysLeft(a.deadline_date, deadlines.today);
              return (
                <li
                  key={a.id}
                  className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0"
                >
                  <span
                    className={`shrink-0 rounded-md px-2 py-0.5 text-xs font-bold ${
                      d !== null && d <= 7
                        ? "bg-red-100 text-red-700 dark:bg-red-500/20 dark:text-red-300"
                        : "bg-black/8 text-black/70 dark:bg-white/10 dark:text-white/70"
                    }`}
                  >
                    {d === null ? "상시" : d === 0 ? "D-DAY" : `D-${d}`}
                  </span>
                  <a
                    href={a.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="min-w-0 flex-1 hover:underline"
                  >
                    <span className="font-medium">{a.title}</span>
                    <span className="ml-2 text-xs text-black/45 dark:text-white/45">
                      {a.agency}
                      {a.deadline_date &&
                        ` · 마감 ${formatDateDot(a.deadline_date)}`}
                    </span>
                  </a>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <footer className="border-t border-black/10 pt-3 text-xs text-black/45 dark:border-white/15 dark:text-white/45">
        본 리포트는 공개 자료를 자동 수집·분석해 생성됩니다. 항목을 누르면 상세
        분석(AI 해석·확인된 사실·KIRO 시사점)으로 이동합니다.
      </footer>
    </div>
  );
}
