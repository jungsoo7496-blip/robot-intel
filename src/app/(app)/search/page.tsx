import { ItemCard } from "@/components/item-card";
import { Pagination } from "@/components/pagination";
import { PAGE_SIZE, searchItems } from "@/lib/data";

export const dynamic = "force-dynamic";

/** 통합검색 (FR-011): pg_trgm 한국어 부분 문자열 검색. */
export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; page?: string }>;
}) {
  const params = await searchParams;
  const q = (params.q ?? "").trim();
  const page = Number(params.page ?? "1") || 1;

  const result = q ? await searchItems(q, page) : null;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">통합검색</h1>

      <form className="flex gap-2">
        <input
          name="q"
          defaultValue={q}
          placeholder="검색어 입력 (2글자 이상) — 예: 휴머노이드, 실증"
          className="w-full max-w-lg rounded border border-black/20 bg-transparent px-3 py-2 dark:border-white/25"
        />
        <button
          type="submit"
          className="rounded bg-foreground px-4 py-2 font-medium text-background"
        >
          검색
        </button>
      </form>

      {!q && (
        <p className="text-sm text-black/50 dark:text-white/50">
          제목, 요약, 확인된 사실, KIRO 시사점, 기관·정책명·키워드에서
          부분 일치로 검색합니다. 조사·어미가 붙어 있어도 찾습니다.
        </p>
      )}

      {result?.tooShort && (
        <p className="text-sm text-red-600">
          2글자 이상의 검색어를 입력해 주세요.
        </p>
      )}

      {result && !result.tooShort && (
        <>
          <p className="text-sm text-black/60 dark:text-white/60">
            “{q}” 검색 결과 {result.total}건
          </p>
          {result.items.length === 0 ? (
            <div className="rounded-lg border border-dashed border-black/20 p-8 text-center text-sm text-black/50 dark:border-white/25 dark:text-white/50">
              일치하는 동향이 없습니다. 다른 검색어를 시도해 보세요.
            </div>
          ) : (
            <div className="grid gap-3">
              {result.items.map((item) => (
                <ItemCard key={item.id} item={item} />
              ))}
            </div>
          )}
          <Pagination
            page={page}
            totalPages={Math.max(1, Math.ceil(result.total / PAGE_SIZE))}
            makeHref={(p) => `/search?q=${encodeURIComponent(q)}&page=${p}`}
          />
        </>
      )}
    </div>
  );
}
