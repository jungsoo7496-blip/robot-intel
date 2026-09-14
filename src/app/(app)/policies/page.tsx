import { formatDateDot } from "@/components/item-card";
import { Pagination } from "@/components/pagination";
import {
  daysLeft,
  getRobotAnnouncementArchive,
  tidyDescription,
} from "@/lib/rnd";

export const dynamic = "force-dynamic";

type SearchParams = { [key: string]: string | undefined };

/**
 * R&D 공고 아카이브 (사용자 지시 2026-08-08):
 * NTIS 국가R&D통합공고 중 로봇 관련 공고를 표 형태로 **계속 누적**한다 —
 * 내년에 같은 사업이 재공고되는 패턴을 추적하기 위해 마감돼도 지우지 않는다.
 * 정렬: 접수 중(마감 먼 순·상시 최상단) → 최근 마감 → 과거 순.
 */
export default async function RndAnnouncementsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await searchParams;
  const page = Number(params.page ?? "1") || 1;
  const { rows, total, today, pageSize } = await getRobotAnnouncementArchive(
    page,
  );
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold">R&D 공고</h1>
        <span className="text-sm text-black/50 dark:text-white/50">
          누적 {total}건
        </span>
      </div>
      <p className="text-sm text-black/60 dark:text-white/60">
        공고명을 누르면 NTIS 원문으로 이동합니다.
      </p>

      {rows.length === 0 ? (
        <div className="rounded-lg border border-dashed border-black/20 p-8 text-center text-sm text-black/50 dark:border-white/25 dark:text-white/50">
          수집된 로봇 관련 공고가 없습니다.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-black/10 dark:border-white/15">
          <table className="w-full min-w-[860px] text-sm">
            <thead className="border-b border-black/10 text-left text-black/60 dark:border-white/15 dark:text-white/60">
              <tr>
                <th className="p-3">상태</th>
                <th className="p-3">공고명</th>
                <th className="p-3">부처·기관</th>
                <th className="p-3">유형</th>
                <th className="p-3">접수기간</th>
                <th className="p-3">공고금액</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => {
                const d = daysLeft(a.deadline_date, today);
                const closed = d !== null && d < 0;
                const desc = tidyDescription(a.description, a.title);
                return (
                  <tr
                    key={a.id}
                    className={`border-b border-black/5 last:border-0 dark:border-white/10 ${
                      closed ? "opacity-55" : ""
                    }`}
                  >
                    <td className="whitespace-nowrap p-3 align-top">
                      {closed ? (
                        <span className="rounded-md bg-black/8 px-2 py-0.5 text-xs font-bold text-black/50 dark:bg-white/10 dark:text-white/50">
                          마감
                        </span>
                      ) : a.status_label === "접수예정" ? (
                        <span className="rounded-md bg-blue-100 px-2 py-0.5 text-xs font-bold text-blue-700 dark:bg-blue-500/20 dark:text-blue-300">
                          접수예정
                        </span>
                      ) : (
                        <span
                          className={`rounded-md px-2 py-0.5 text-xs font-bold ${
                            d !== null && d <= 7
                              ? "bg-red-100 text-red-700 dark:bg-red-500/20 dark:text-red-300"
                              : "bg-black/8 text-black/70 dark:bg-white/10 dark:text-white/70"
                          }`}
                        >
                          {d === null ? "상시" : d === 0 ? "D-DAY" : `D-${d}`}
                        </span>
                      )}
                    </td>
                    <td className="p-3 align-top">
                      <a
                        href={a.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="font-medium leading-snug underline-offset-2 hover:underline"
                      >
                        {a.title}
                      </a>
                      {desc && (
                        <div className="mt-0.5 line-clamp-1 max-w-xl text-xs text-black/50 dark:text-white/50">
                          {desc}
                        </div>
                      )}
                    </td>
                    <td className="p-3 align-top">
                      {a.agency ?? "—"}
                      {a.org_name && a.org_name !== a.agency && (
                        <div className="text-xs text-black/50 dark:text-white/50">
                          {a.org_name}
                        </div>
                      )}
                    </td>
                    <td className="whitespace-nowrap p-3 align-top">
                      {a.notice_type ?? "—"}
                    </td>
                    <td className="whitespace-nowrap p-3 align-top">
                      {a.apply_start || a.deadline_date ? (
                        <>
                          {a.apply_start ? formatDateDot(a.apply_start) : "?"} ~{" "}
                          {a.deadline_date
                            ? formatDateDot(a.deadline_date)
                            : "상시"}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="whitespace-nowrap p-3 align-top">
                      {a.budget_text ? `${a.budget_text}억 원` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <Pagination
        page={page}
        totalPages={totalPages}
        makeHref={(p) => `/policies?page=${p}`}
      />

      <p className="text-xs text-black/45 dark:text-white/45">
        출처: NTIS 국가R&D통합공고 (ntis.go.kr) · 마감(D-day)은 열람 시점
        기준으로 계산됩니다
      </p>
    </div>
  );
}
