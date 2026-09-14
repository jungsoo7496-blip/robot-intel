import Link from "next/link";

import { formatDate } from "@/components/item-card";
import { getBriefs, type BriefListRow } from "@/lib/briefs";

export const dynamic = "force-dynamic";

/** 제목의 "(기간)" 꼬리를 떼어 기간 표기와의 중복을 없앤다 (UI/UX 개선 4). */
function cleanTitle(title: string) {
  return title.replace(/\s*\([\d.\s~-]+\)\s*$/, "").trim() || title;
}

function periodLabel(b: BriefListRow) {
  if (!b.brief_periods) return "";
  return `${b.brief_periods.period_start.replaceAll("-", ".")} ~ ${b.brief_periods.period_end.replaceAll("-", ".")}`;
}

function itemCount(b: BriefListRow) {
  return b.brief_items?.[0]?.count ?? 0;
}

/** 격주 브리프 목록 (FR-012): 최신 호는 요약과 함께 강조 표시. */
export default async function BriefsPage() {
  const briefs = await getBriefs();
  const [latest, ...rest] = briefs;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">주간 리포트</h1>
      <p className="text-sm text-black/60 dark:text-white/60">
        한 주(월~일)의 로봇 정책·산업·기술 동향 중 핵심 변화를 선별해 AI가
        매주 월요일 자동 발행합니다.
      </p>

      {!latest ? (
        <div className="rounded-xl border border-dashed border-black/20 p-8 text-center text-sm text-black/50 dark:border-white/25 dark:text-white/50">
          아직 발행된 브리프가 없습니다. 격주 일정에 따라 자동 발행됩니다.
        </div>
      ) : (
        <div className="grid gap-3">
          <Link
            href={`/briefs/${latest.id}`}
            className="block rounded-xl border border-black/15 bg-white p-5 transition hover:border-black/35 hover:shadow-md dark:border-white/20 dark:bg-white/[0.03] dark:hover:border-white/45"
          >
            <div className="text-sm text-black/50 dark:text-white/50">
              최신 · {periodLabel(latest)} · 발행 {formatDate(latest.published_at)}
              {latest.brief_periods?.cadence === "BIWEEKLY" && " · 격주"}
              {latest.version > 1 && ` · v${latest.version}`}
            </div>
            <div className="mt-1 text-lg font-bold">
              {cleanTitle(latest.title)}
            </div>
            {latest.section_summary_policy?.one_page_summary && (
              <p className="mt-2 line-clamp-2 leading-relaxed text-black/75 dark:text-white/75">
                {latest.section_summary_policy.one_page_summary.replace(
                  /\s*\[\d+\]/g,
                  "",
                )}
              </p>
            )}
            <p className="mt-2 text-xs text-black/45 dark:text-white/45">
              동향 {itemCount(latest)}건 기반
            </p>
          </Link>

          {rest.map((b) => (
            <Link
              key={b.id}
              href={`/briefs/${b.id}`}
              className="flex flex-wrap items-baseline justify-between gap-2 rounded-xl border border-black/10 px-5 py-3.5 transition hover:border-black/30 dark:border-white/12 dark:hover:border-white/35"
            >
              <span className="font-semibold">{cleanTitle(b.title)}</span>
              <span className="text-sm text-black/50 dark:text-white/50">
                {periodLabel(b)} · 동향 {itemCount(b)}건
                {b.brief_periods?.cadence === "BIWEEKLY" && " · 격주"}
                {b.version > 1 && ` · v${b.version}`}
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
