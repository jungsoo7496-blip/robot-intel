import Link from "next/link";

/**
 * 클릭형 필터 칩 (사용자 피드백 4): 드롭다운 대신 한 번 클릭으로 선택·해제.
 * 나머지 쿼리 파라미터는 유지한다.
 */
export function ChipFilter({
  basePath,
  paramName,
  label,
  options,
  params,
  labels,
  inline = false,
  scrollable = false,
}: {
  basePath: string;
  paramName: string;
  label: string;
  options: readonly string[];
  params: { [key: string]: string | undefined };
  /** 화면 표시용 축약 라벨 (값→라벨). 없으면 값 그대로. */
  labels?: Record<string, string>;
  /** true면 라벨 고정폭 없이 흐름에 배치 (여러 필터를 한 줄에) */
  inline?: boolean;
  /** true면 줄바꿈 없이 한 줄 가로 스크롤 (옵션이 많은 필터 — 예: 연도) */
  scrollable?: boolean;
}) {
  const current = params[paramName];

  function hrefWith(value?: string) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (!v || k === "page" || k === paramName) continue;
      q.set(k, v);
    }
    if (value) q.set(paramName, value);
    const qs = q.toString();
    return qs ? `${basePath}?${qs}` : basePath;
  }

  return (
    <div
      className={`flex items-center gap-1.5 ${
        scrollable
          ? "overflow-x-auto whitespace-nowrap pb-1 [scrollbar-width:thin] [&>a]:shrink-0"
          : "flex-wrap"
      }`}
    >
      <span
        className={`mr-1 shrink-0 text-sm font-medium text-black/60 dark:text-white/60 ${
          inline ? "" : "w-16"
        }`}
      >
        {label}
      </span>
      <Link
        href={hrefWith(undefined)}
        className={`rounded-full px-3 py-1 text-sm transition ${
          !current
            ? "bg-foreground font-medium text-background"
            : "border border-black/15 hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
        }`}
      >
        전체
      </Link>
      {options.map((option) => (
        <Link
          key={option}
          href={hrefWith(option)}
          className={`rounded-full px-3 py-1 text-sm transition ${
            current === option
              ? "bg-foreground font-medium text-background"
              : "border border-black/15 hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
          }`}
        >
          {labels?.[option] ?? option}
        </Link>
      ))}
    </div>
  );
}
