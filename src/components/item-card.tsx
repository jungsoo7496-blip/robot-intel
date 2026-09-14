import Link from "next/link";

import { shortRobotField } from "@/types/analysis";
import type { PublishedItem } from "@/types/analysis";

const IMPORTANCE_STYLE: Record<string, string> = {
  높음: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200",
  보통: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-200",
  낮음: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300",
};

const CATEGORY_STYLE: Record<string, string> = {
  정책: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200",
  산업: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
  기술: "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-200",
};

export function Badge({
  children,
  className = "bg-black/5 text-black/60 dark:bg-white/10 dark:text-white/60",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${className}`}
    >
      {children}
    </span>
  );
}

export function CategoryBadge({ category }: { category: string }) {
  return <Badge className={CATEGORY_STYLE[category]}>{category}</Badge>;
}

export function ImportanceBadge({ importance }: { importance: string }) {
  return <Badge className={IMPORTANCE_STYLE[importance]}>{importance}</Badge>;
}

// 서버(Vercel)는 UTC라 타임존을 명시하지 않으면 9시간 어긋난다 (2026-08-08 버그)
export function formatDate(value: string | null | undefined) {
  if (!value) return "날짜 미상";
  return new Date(value).toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "Asia/Seoul",
  });
}

/** YYYY.MM.DD 고정폭 표기 (표 안에서 줄바꿈 방지용) — KST 기준. */
export function formatDateDot(value: string | null | undefined) {
  if (!value) return null;
  const d = new Date(new Date(value).getTime() + 9 * 60 * 60 * 1000);
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(d.getUTCDate()).padStart(2, "0");
  return `${d.getUTCFullYear()}.${mm}.${dd}`;
}

/**
 * 동향 카드 (FR-008): 요약 우선, 카드 높이는 압축 (UI/UX 개선 2).
 */
export function ItemCard({ item }: { item: PublishedItem }) {
  return (
    <Link
      href={`/trends/${item.id}`}
      className="block rounded-xl border border-black/10 bg-white px-4 py-3.5 transition hover:border-black/25 hover:shadow-md dark:border-white/12 dark:bg-white/[0.03] dark:hover:border-white/35"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <CategoryBadge category={item.category} />
        <Badge>{item.region}</Badge>
        <Badge>{shortRobotField(item.robot_field)}</Badge>
        <ImportanceBadge importance={item.importance} />
      </div>

      <h3 className="mt-1.5 text-[16px] font-bold leading-snug">{item.title}</h3>

      {item.one_line_summary && (
        <p className="mt-1 line-clamp-2 leading-relaxed text-black/75 dark:text-white/75">
          {item.one_line_summary}
        </p>
      )}

      {item.kiro_implication_excerpt && (
        <p className="mt-1.5 line-clamp-1 border-l-2 border-amber-400 pl-2.5 text-sm text-black/60 dark:text-white/60">
          <span className="font-medium text-amber-700 dark:text-amber-400">
            KIRO 시사점
          </span>{" "}
          {item.kiro_implication_excerpt}
        </p>
      )}

      <p className="mt-2 text-xs text-black/45 dark:text-white/45">
        {formatDate(item.source_published_at ?? item.published_at)} ·{" "}
        {item.representative_source_name ?? "출처 미상"} · 근거{" "}
        {item.evidence_level}
        {item.related_source_count > 1 &&
          ` · 관련 출처 ${item.related_source_count}건`}
      </p>
    </Link>
  );
}
