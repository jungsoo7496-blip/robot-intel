import Link from "next/link";

import { ChipFilter } from "@/components/chip-filter";
import { ItemCard } from "@/components/item-card";
import { Pagination } from "@/components/pagination";
import { PAGE_SIZE, getItems } from "@/lib/data";
import {
  CATEGORIES,
  KIRO_AXES,
  REGIONS,
  ROBOT_FIELDS,
  ROBOT_FIELD_SHORT,
} from "@/types/analysis";

export const dynamic = "force-dynamic";

type SearchParams = { [key: string]: string | undefined };

const DAY_OPTIONS = [
  ["1", "1일"],
  ["3", "3일"],
  ["7", "7일"],
  ["14", "2주"],
  ["30", "1개월"],
  ["90", "3개월"],
] as const;

const SORT_OPTIONS = [
  ["latest", "최신순"],
  ["importance", "중요도순"],
  ["kiro", "KIRO 관련도순"],
] as const;

/** 최신 동향 (FR-008): 칩 2줄 + 하단 상세 필터·정렬 (사용자 피드백). */
export default async function TrendsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await searchParams;
  const page = Number(params.page ?? "1") || 1;
  const filters = {
    category: params.category || undefined,
    region: params.region || undefined,
    robot_field: params.robot_field || undefined,
    importance: params.importance || undefined,
    evidence_level: params.evidence_level || undefined,
    kiro_axis: params.kiro_axis || undefined,
    days: params.days ? Number(params.days) : undefined,
    month: params.month || undefined,
    // 일간 리포트 연계 범위 — ISO 형태만 통과 (외부 리뷰 P2-2)
    from: /^\d{4}-\d{2}-\d{2}T[\d:.]+Z?$/.test(params.from ?? "")
      ? params.from
      : undefined,
    to: /^\d{4}-\d{2}-\d{2}T[\d:.]+Z?$/.test(params.to ?? "")
      ? params.to
      : undefined,
    sort: params.sort || undefined,
    page,
  };
  const { items, total } = await getItems(filters);
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const baseQuery = new URLSearchParams(
    Object.entries(params).filter(
      ([k, v]) => v && k !== "page",
    ) as [string, string][],
  ).toString();

  const hasFilter = Object.entries(params).some(([k, v]) => v && k !== "page");

  // 기간 조건은 상호배타 (외부 리뷰): days / month / from·to 중 하나만 유효.
  // 서로 겹쳐 AND되면 "3일을 눌렀는데 일간 24시간 교집합"이 되는 모순 방지.
  const PERIOD_PARAMS = ["days", "month", "from", "to"];

  // 상세 필터 form에서 유지할 칩 파라미터 (기간 조건은 form이 새로 정한다)
  const chipParams = Object.entries(params).filter(
    ([k, v]) =>
      v && !["page", "kiro_axis", "sort", ...PERIOD_PARAMS].includes(k),
  ) as [string, string][];

  return (
    <div className="space-y-5">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold">최신 동향</h1>
        <span className="text-sm text-black/50 dark:text-white/50">
          총 {total}건
        </span>
      </div>

      <div className="space-y-2.5 rounded-xl border border-black/10 p-4 dark:border-white/12">
        {/* 1줄: 분야 + 지역 */}
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <ChipFilter
            basePath="/trends"
            paramName="category"
            label="분야"
            options={CATEGORIES}
            params={params}
            inline
          />
          <span className="hidden h-4 w-px bg-black/15 sm:block dark:bg-white/20" />
          <ChipFilter
            basePath="/trends"
            paramName="region"
            label="지역"
            options={REGIONS}
            params={params}
            inline
          />
        </div>

        {/* 2줄: 로봇 분야 (축약 라벨) */}
        <ChipFilter
          basePath="/trends"
          paramName="robot_field"
          label="로봇"
          options={ROBOT_FIELDS}
          labels={ROBOT_FIELD_SHORT}
          params={params}
          inline
        />

        {/* 3줄: 기간 칩 + 상세 필터(월·KIRO 업무·정렬) */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-black/5 pt-2.5 dark:border-white/10">
          <span className="text-sm font-medium text-black/60 dark:text-white/60">
            기간
          </span>
          {DAY_OPTIONS.map(([value, label]) => {
            // 기간 칩 선택 = 다른 기간 조건(월·일간 from/to) 전부 해제
            const q = new URLSearchParams(
              Object.entries(params).filter(
                ([k, v]) => v && k !== "page" && !PERIOD_PARAMS.includes(k),
              ) as [string, string][],
            );
            const active = params.days === value;
            if (!active) q.set("days", value);
            return (
              <Link
                key={value}
                href={`/trends${q.toString() ? `?${q}` : ""}`}
                className={`rounded-full px-3 py-1 text-sm transition ${
                  active
                    ? "bg-foreground font-medium text-background"
                    : "border border-black/15 hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
                }`}
              >
                {label}
              </Link>
            );
          })}

          <form className="ml-auto flex flex-wrap items-center gap-2">
            {chipParams.map(([k, v]) => (
              <input key={k} type="hidden" name={k} value={v} />
            ))}
            <select
              name="month"
              defaultValue={params.month ?? ""}
              className="rounded border border-black/15 bg-transparent px-2 py-1 text-sm dark:border-white/20 dark:[&>option]:bg-neutral-900"
            >
              <option value="">특정 월</option>
              {Array.from({ length: 12 }, (_, i) => {
                // 서버(UTC)에서도 KST 기준 월이 나오도록 +9h 보정
                const d = new Date(new Date().getTime() + 9 * 60 * 60 * 1000);
                d.setUTCMonth(d.getUTCMonth() - i);
                const value = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
                return (
                  <option key={value} value={value}>
                    {d.getUTCFullYear()}.{d.getUTCMonth() + 1}
                  </option>
                );
              })}
            </select>
            <select
              name="kiro_axis"
              defaultValue={params.kiro_axis ?? ""}
              className="rounded border border-black/15 bg-transparent px-2 py-1 text-sm dark:border-white/20 dark:[&>option]:bg-neutral-900"
            >
              <option value="">KIRO 업무 전체</option>
              {KIRO_AXES.map((axis) => (
                <option key={axis} value={axis}>
                  {axis}
                </option>
              ))}
            </select>
            <select
              name="sort"
              defaultValue={params.sort ?? "latest"}
              className="rounded border border-black/15 bg-transparent px-2 py-1 text-sm dark:border-white/20 dark:[&>option]:bg-neutral-900"
            >
              {SORT_OPTIONS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <button
              type="submit"
              className="rounded border border-black/15 px-2.5 py-1 text-sm hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
            >
              적용
            </button>
          </form>

          {hasFilter && (
            <Link
              href="/trends"
              className="text-sm text-black/50 underline dark:text-white/50"
            >
              초기화
            </Link>
          )}
        </div>
      </div>

      {items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-black/20 p-10 text-center text-sm text-black/50 dark:border-white/25 dark:text-white/50">
          조건에 맞는 동향이 없습니다. 필터를 조정해 보세요.
          {filters.kiro_axis && (
            <span className="mt-1 block">
              (KIRO 업무 필터는 새 분석 기준이라, 재분석이 진행될수록 결과가
              늘어납니다)
            </span>
          )}
        </div>
      ) : (
        <div className="grid gap-4">
          {items.map((item) => (
            <ItemCard key={item.id} item={item} />
          ))}
        </div>
      )}

      <Pagination
        page={page}
        totalPages={totalPages}
        makeHref={(p) => `/trends?${baseQuery}&page=${p}`}
      />
    </div>
  );
}
