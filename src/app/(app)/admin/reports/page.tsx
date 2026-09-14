import { createServiceRoleClient } from "@/lib/supabase/server";

import { toggleReportVisibility } from "../actions";

export const dynamic = "force-dynamic";

const RELEVANCE_BADGE: Record<string, string> = {
  DIRECT: "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300",
  RELATED: "bg-blue-100 text-blue-800 dark:bg-blue-500/20 dark:text-blue-300",
  WEAK: "bg-amber-100 text-amber-900 dark:bg-amber-500/20 dark:text-amber-300",
  EXCLUDE: "bg-red-100 text-red-700 dark:bg-red-500/20 dark:text-red-300",
};

/**
 * 보고서 관리 (보고서 스펙 구현순서 15) — 최근 수집 문서의 분석·공개 상태를
 * 확인하고 수동으로 노출을 뒤집는다 (manual_override — 자동 판정이 다시
 * 덮어쓰지 않음). 상세 CRUD·연결 테스트는 Phase 1.5.
 */
export default async function AdminReportsPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string }>;
}) {
  const params = await searchParams;
  const page = Number(params.page ?? "1") || 1;
  const pageSize = 30;

  const supabase = createServiceRoleClient();
  const { data, count } = await supabase
    .from("report_documents")
    .select(
      `id, canonical_title, institution, published_year, analysis_status,
       is_visible, manual_override, created_at,
       analysis:report_analyses!report_documents_current_analysis_fk (
         robot_relevance, report_type
       ),
       occurrence:report_occurrences!report_documents_preferred_occurrence_id_fkey (
         access_status, detail_url
       )`,
      { count: "exact" },
    )
    .order("created_at", { ascending: false })
    .range((page - 1) * pageSize, page * pageSize - 1);

  type Row = NonNullable<typeof data>[number] & {
    analysis: { robot_relevance: string; report_type: string | null } | null;
    occurrence: { access_status: string; detail_url: string } | null;
  };
  const rows = (data ?? []) as unknown as Row[];
  const totalPages = Math.max(1, Math.ceil((count ?? 0) / pageSize));

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">보고서 관리</h1>
      <p className="text-sm text-black/60 dark:text-white/60">
        공개 여부는 배치가 자동 판정(접근 가능 + DIRECT/RELATED)하며, 여기서
        수동으로 바꾸면 이후 재분석이 덮어쓰지 않습니다. 전체 {count ?? 0}건.
      </p>

      <div className="overflow-x-auto rounded-lg border border-black/10 dark:border-white/15">
        <table className="w-full min-w-[900px] text-sm">
          <thead className="border-b border-black/10 text-left text-black/60 dark:border-white/15 dark:text-white/60">
            <tr>
              <th className="p-3">보고서</th>
              <th className="p-3">관련성</th>
              <th className="p-3">유형</th>
              <th className="p-3">접근</th>
              <th className="p-3">분석</th>
              <th className="p-3">공개</th>
              <th className="p-3"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.id}
                className="border-b border-black/5 last:border-0 dark:border-white/10"
              >
                <td className="max-w-md p-3">
                  <a
                    href={r.occurrence?.detail_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-medium leading-snug underline-offset-2 hover:underline"
                  >
                    {r.canonical_title}
                  </a>
                  <div className="text-xs text-black/45 dark:text-white/45">
                    {r.institution ?? "기관 미상"} ·{" "}
                    {r.published_year ?? "연도 미상"}
                  </div>
                </td>
                <td className="p-3">
                  {r.analysis ? (
                    <span
                      className={`rounded-md px-2 py-0.5 text-xs font-bold ${
                        RELEVANCE_BADGE[r.analysis.robot_relevance] ?? ""
                      }`}
                    >
                      {r.analysis.robot_relevance}
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="whitespace-nowrap p-3">
                  {r.analysis?.report_type ?? "—"}
                </td>
                <td className="whitespace-nowrap p-3 text-xs">
                  {r.occurrence?.access_status ?? "—"}
                </td>
                <td className="whitespace-nowrap p-3 text-xs">
                  {r.analysis_status}
                </td>
                <td className="p-3">
                  {r.is_visible ? "공개" : "비공개"}
                  {r.manual_override && (
                    <span className="ml-1 text-xs text-amber-600 dark:text-amber-400">
                      (수동)
                    </span>
                  )}
                </td>
                <td className="p-3">
                  <form action={toggleReportVisibility}>
                    <input type="hidden" name="id" value={r.id} />
                    <input
                      type="hidden"
                      name="show"
                      value={String(!r.is_visible)}
                    />
                    <button
                      type="submit"
                      className="rounded-md border border-black/15 px-2 py-1 text-xs hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
                    >
                      {r.is_visible ? "비공개로" : "공개로"}
                    </button>
                  </form>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex gap-2 text-sm">
          {page > 1 && (
            <a
              href={`/admin/reports?page=${page - 1}`}
              className="underline-offset-2 hover:underline"
            >
              ← 이전
            </a>
          )}
          <span className="text-black/50 dark:text-white/50">
            {page} / {totalPages}
          </span>
          {page < totalPages && (
            <a
              href={`/admin/reports?page=${page + 1}`}
              className="underline-offset-2 hover:underline"
            >
              다음 →
            </a>
          )}
        </div>
      )}
    </div>
  );
}
