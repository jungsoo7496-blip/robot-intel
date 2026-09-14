import Link from "next/link";

/**
 * 번호형 페이지네이션 (사용자 요청 2026-08-08):
 * 첫 페이지 / 이전 / [현재±2 번호 5개] / 다음.
 * 예: 7페이지 열람 중이면 5 6 [7] 8 9 가 보인다 (경계에서는 5개 유지하며 이동).
 */
export function Pagination({
  page,
  totalPages,
  makeHref,
}: {
  page: number;
  totalPages: number;
  makeHref: (page: number) => string;
}) {
  if (totalPages <= 1) return null;

  // 현재를 가운데 두되 양끝에서는 창을 밀어 항상 최대 5개 유지
  const windowStart = Math.max(1, Math.min(page - 2, totalPages - 4));
  const numbers = Array.from(
    { length: Math.min(5, totalPages) },
    (_, i) => windowStart + i,
  );

  const btn =
    "rounded border border-black/20 px-3 py-1 hover:border-black/40 dark:border-white/25 dark:hover:border-white/45";
  const disabled =
    "rounded border border-black/10 px-3 py-1 text-black/30 dark:border-white/10 dark:text-white/30";

  return (
    <nav
      className="flex flex-wrap items-center justify-center gap-1.5 text-sm"
      aria-label="페이지 이동"
    >
      {page > 1 ? (
        <>
          <Link href={makeHref(1)} className={btn}>
            처음
          </Link>
          <Link href={makeHref(page - 1)} className={btn}>
            이전
          </Link>
        </>
      ) : (
        <>
          <span className={disabled}>처음</span>
          <span className={disabled}>이전</span>
        </>
      )}

      {numbers.map((n) =>
        n === page ? (
          <span
            key={n}
            aria-current="page"
            className="rounded bg-foreground px-3 py-1 font-semibold text-background"
          >
            {n}
          </span>
        ) : (
          <Link key={n} href={makeHref(n)} className={btn}>
            {n}
          </Link>
        ),
      )}

      {page < totalPages ? (
        <Link href={makeHref(page + 1)} className={btn}>
          다음
        </Link>
      ) : (
        <span className={disabled}>다음</span>
      )}

      <span className="ml-2 text-xs text-black/45 dark:text-white/45">
        {page} / {totalPages}
      </span>
    </nav>
  );
}
