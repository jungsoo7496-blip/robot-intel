import Link from "next/link";

import { requireOperator } from "@/lib/auth";
import { createServiceRoleClient } from "@/lib/supabase/server";

import { toggleSource, triggerCollectNow } from "../actions";
import { guessKeywordOfSource } from "../search-keywords/keyword-fields";
import { DeleteSourceButton } from "./delete-source-button";
import {
  FETCH_METHOD_LABELS,
  defaultFormValues,
  describeAdapter,
  formValuesFromRow,
  isKeywordManaged,
  type SourceRow,
} from "./source-fields";
import { SourceForm } from "./source-form";

export const dynamic = "force-dynamic";

type SearchParams = {
  new?: string;
  edit?: string;
  /** 삭제·비활성화 결과 코드 (문구는 서버가 조립한다) */
  done?: string;
  id?: string;
  name?: string;
};

function one(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

/**
 * 처리 결과 안내 문구. 주소창에서 받는 것은 코드(done)와 대상 식별자뿐이고
 * 문장은 여기서 만든다 — 자유 문자열을 그대로 성공 배너에 띄우면 링크 하나로
 * 운영자에게 가짜 시스템 메시지를 보여줄 수 있다.
 */
function buildNotice(
  params: SearchParams,
  sources: SourceRow[],
  rawCounts: Map<string, number>,
): string | undefined {
  const done = one(params.done);
  if (done === "disabled") {
    const id = one(params.id);
    const row = id ? sources.find((s) => s.id === id) : undefined;
    if (!row || row.is_active) return undefined; // DB 상태와 어긋나면 표시하지 않는다
    const n = (rawCounts.get(row.id) ?? 0).toLocaleString("ko-KR");
    return `‘${row.name}’은(는) 수집 기록 ${n}건이 있어 삭제 대신 비활성화했습니다. 이미 수집된 기사는 그대로 남습니다.`;
  }
  if (done === "deleted") {
    // 삭제된 행은 조회로 확인할 수 없어 이름만 받되, 고정 문장 안에 가둔다.
    const name = one(params.name)?.trim().slice(0, 120);
    return name ? `‘${name}’을(를) 삭제했습니다.` : "수집원을 삭제했습니다.";
  }
  return undefined;
}

function formatDateTime(value: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString("ko-KR", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    timeZone: "Asia/Seoul",
  });
}

const smallButtonClass =
  "whitespace-nowrap rounded border border-black/20 px-2 py-1 text-xs hover:bg-black/5 dark:border-white/25 dark:hover:bg-white/10";

/**
 * 뉴스 수집원 (FR-002, tasks §14.3) — RSS 피드·목록 페이지 추가·편집·삭제·URL 확인.
 *
 * 네이버·구글 뉴스 '검색어' 수집원(sources.managed_by='keyword_search')은 이
 * 목록에서 빼고 /admin/search-keywords가 관리한다 (사용자 요구 1-1·1-3).
 * 그 행들의 이름·주소는 다음 수집 때 배치가 검색어 목록을 보고 다시 만들기
 * 때문에 여기서 고쳐도 되돌아간다.
 */
export default async function AdminSourcesPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  // 레이아웃에서 한 번 검사하지만, 소프트 내비게이션으로 이 페이지만 다시
  // 그려질 때도 권한을 확인한다 (service role 조회 전에).
  await requireOperator();
  const params = await searchParams;
  const supabase = createServiceRoleClient();
  const { data } = await supabase
    .from("sources")
    .select("*")
    .order("priority")
    .order("name");
  const allSources = (data ?? []) as SourceRow[];

  // 검색 키워드 화면이 소유하는 행은 목록에서 뺀다 (요구 1-3).
  const managedCount = allSources.filter(isKeywordManaged).length;
  const sources = allSources.filter((s) => !isKeywordManaged(s));

  // 마이그레이션 26 적용 전에는 태깅이 없어 네이버·구글 검색 행이 아직 여기
  // 남아 있다. 사라진 것처럼 보이지 않게 목록에는 두되 '옮겨질 행'으로 표시한다.
  // (sources는 이미 isKeywordManaged 행을 걸러낸 목록이다)
  const pendingMoveIds = new Set(
    sources.filter((s) => guessKeywordOfSource(s) !== null).map((s) => s.id),
  );

  // 수집원별 수집 기록 건수 — 삭제 시 '삭제 / 비활성화' 판단과 안내에 쓴다.
  // (source_id, fetched_at) 인덱스가 있어 head count는 가볍다.
  const countEntries = await Promise.all(
    sources.map(async (s) => {
      const { count } = await supabase
        .from("raw_items")
        .select("id", { count: "exact", head: true })
        .eq("source_id", s.id);
      return [s.id, count ?? 0] as const;
    }),
  );
  const rawCounts = new Map<string, number>(countEntries);

  const editing = params.edit
    ? sources.find((s) => s.id === params.edit)
    : undefined;
  // 검색어로 만들어진 행을 편집하려 한 경우 — '없는 수집원'이라고 하면 오해한다
  const editingManaged =
    params.edit && !editing
      ? allSources.find((s) => s.id === params.edit && isKeywordManaged(s))
      : undefined;
  const creating = !editing && params.new === "1";
  const notice = buildNotice(params, sources, rawCounts);

  // 우선순위 분포 — 폼의 기본값·안내에 쓴다 (collect.py 수집 순서 tie-breaker 전용)
  const priorityCounts = new Map<number, number>();
  for (const s of sources) {
    priorityCounts.set(s.priority, (priorityCounts.get(s.priority) ?? 0) + 1);
  }
  const priorityHint =
    [...priorityCounts.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([p, n]) => `${p}: ${n}개`)
      .join(" · ") || "없음";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">뉴스 수집원</h1>
        <div className="flex flex-wrap items-center gap-2">
          <Link
            href="/admin/search-keywords"
            className="rounded-lg border border-black/20 px-4 py-2 text-sm font-semibold hover:bg-black/5 dark:border-white/25 dark:hover:bg-white/10"
          >
            검색 키워드
          </Link>
          <Link
            href="/admin/sources?new=1"
            className="rounded-lg border border-black/20 px-4 py-2 text-sm font-semibold hover:bg-black/5 dark:border-white/25 dark:hover:bg-white/10"
          >
            새 수집원 추가
          </Link>
          {/* 수동 수집 (사용자 요청): 정기 배치와 동일한 워크플로를 즉시 실행 */}
          <form action={triggerCollectNow}>
            <button
              type="submit"
              className="rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background hover:opacity-90"
            >
              지금 수집 실행
            </button>
          </form>
        </div>
      </div>
      <p className="text-sm text-black/60 dark:text-white/60">
        <strong>RSS 피드 주소</strong>와 <strong>목록 페이지</strong>를 여기서
        추가·편집·삭제합니다. 네이버·구글 뉴스는 주소가 아니라 검색어로 모으므로{" "}
        <Link href="/admin/search-keywords" className="underline">
          검색 키워드
        </Link>{" "}
        화면에서 관리합니다. 비활성화하면 다음 수집 주기부터 제외되고, 수집 기록이
        있는 수집원은 삭제 대신 비활성화됩니다(기사의 출처 정보를 지키기 위해).
        &ldquo;지금 수집 실행&rdquo;은 30초~2분 내 시작되며, 완료 후 이 화면의
        &ldquo;마지막 성공&rdquo; 시각으로 확인할 수 있습니다.
      </p>

      {managedCount > 0 && (
        <p className="rounded-lg border border-black/10 bg-black/[0.02] px-4 py-2 text-sm text-black/60 dark:border-white/15 dark:bg-white/[0.04] dark:text-white/60">
          검색어로 모으는 수집원 {managedCount}개(네이버·구글)는 이 목록에
          표시하지 않습니다 —{" "}
          <Link href="/admin/search-keywords" className="underline">
            검색 키워드
          </Link>{" "}
          화면에서 보고 고칠 수 있습니다.
        </p>
      )}
      {managedCount === 0 && pendingMoveIds.size > 0 && (
        <p className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          아래 목록의 네이버·구글 검색 수집원 {pendingMoveIds.size}개는 곧{" "}
          <Link href="/admin/search-keywords" className="underline">
            검색 키워드
          </Link>{" "}
          화면으로 옮겨집니다(데이터베이스 변경 적용 전). 그때까지는 여기서
          이름·우선순위만 고쳐 주세요.
        </p>
      )}

      {notice && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-green-300 bg-green-50 px-4 py-3 text-sm text-green-800 dark:border-green-800 dark:bg-green-950/40 dark:text-green-300">
          <span>{notice}</span>
          <Link href="/admin/sources" className="text-xs underline">
            닫기
          </Link>
        </div>
      )}

      {(creating || editing) && (
        <section className="space-y-3 rounded-lg border border-black/10 p-4 dark:border-white/15">
          <h2 className="text-lg font-semibold">
            {editing ? `수집원 편집 — ${editing.name}` : "새 수집원 추가"}
          </h2>
          <SourceForm
            key={editing ? editing.id : "new"}
            mode={editing ? "edit" : "create"}
            initial={editing ? formValuesFromRow(editing) : defaultFormValues()}
            priorityHint={priorityHint}
          />
        </section>
      )}
      {params.edit && !editing && editingManaged && (
        <p className="text-sm text-black/60 dark:text-white/60">
          ‘{editingManaged.name}’은(는) 검색어로 만들어진 수집원이라 여기서
          고칠 수 없습니다.{" "}
          <Link href="/admin/search-keywords" className="underline">
            검색 키워드
          </Link>{" "}
          화면에서 바꿔 주세요.
        </p>
      )}
      {params.edit && !editing && !editingManaged && (
        <p className="text-sm text-red-600">
          편집하려는 수집원을 찾을 수 없습니다. 이미 삭제됐을 수 있습니다.
        </p>
      )}

      {/*
        열 폭은 colgroup(%)으로 고정한다 (table-fixed) — 긴 주소·이름이 들어와도
        표가 가로로 넘치지 않고 셀 안에서 잘린다. 잘린 값은 마우스를 올리면
        전체가 보이고(title), 전체 주소는 [편집] 화면에도 있다.
      */}
      <div className="overflow-x-auto rounded-lg border border-black/10 dark:border-white/15">
        <table className="w-full min-w-[880px] table-fixed text-sm">
          <colgroup>
            <col className="w-[17%]" />
            <col className="w-[11%]" />
            <col className="w-[18%]" />
            <col className="w-[9%]" />
            <col className="w-[7%]" />
            <col className="w-[17%]" />
            <col className="w-[6%]" />
            <col className="w-[15%]" />
          </colgroup>
          <thead className="border-b border-black/10 text-left text-black/60 dark:border-white/15 dark:text-white/60">
            <tr>
              <th className="p-2">이름</th>
              <th className="p-2">유형 · 지역·언어</th>
              <th className="p-2">수집 방식</th>
              <th className="p-2">우선순위 · 간격</th>
              <th className="p-2">수집 기록</th>
              <th className="p-2">마지막 성공 · 실패</th>
              <th className="p-2">상태</th>
              <th className="p-2"></th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => {
              const failing =
                s.last_failure_at &&
                (!s.last_success_at ||
                  new Date(s.last_failure_at) > new Date(s.last_success_at));
              const adapter = describeAdapter(s);
              const rawCount = rawCounts.get(s.id) ?? 0;
              return (
                <tr
                  key={s.id}
                  className={`border-b border-black/5 last:border-0 dark:border-white/10 ${
                    editing?.id === s.id ? "bg-black/[0.03] dark:bg-white/[0.06]" : ""
                  }`}
                >
                  <td className="p-2 align-top">
                    <div className="break-words font-medium">{s.name}</div>
                    {pendingMoveIds.has(s.id) && (
                      <div className="text-xs text-amber-700 dark:text-amber-400">
                        검색 키워드 화면으로 이동 예정
                      </div>
                    )}
                    {s.notes && (
                      <div
                        className="truncate text-xs text-black/40 dark:text-white/40"
                        title={s.notes}
                      >
                        {s.notes}
                      </div>
                    )}
                  </td>
                  <td className="p-2 align-top">
                    <div className="break-words">{s.source_type}</div>
                    <div className="text-xs text-black/50 dark:text-white/50">
                      {s.country_region} · {s.language}
                    </div>
                  </td>
                  <td className="p-2 align-top">
                    <div>{FETCH_METHOD_LABELS[s.fetch_method] ?? s.fetch_method}</div>
                    {/* 사이트 이름만 — 전체 주소는 마우스 오버와 [편집] 화면에서 */}
                    <div
                      className="truncate text-xs text-black/50 dark:text-white/50"
                      title={adapter.fullUrl ?? adapter.primary}
                    >
                      {adapter.primary}
                    </div>
                    {adapter.secondary && (
                      <div
                        className="truncate text-xs text-black/40 dark:text-white/40"
                        title={adapter.secondary}
                      >
                        {adapter.secondary}
                      </div>
                    )}
                  </td>
                  <td className="p-2 align-top">
                    {s.priority} · {s.fetch_interval_minutes}분
                  </td>
                  <td className="p-2 align-top">
                    {rawCount.toLocaleString("ko-KR")}건
                  </td>
                  <td className="p-2 align-top text-xs">
                    <div className="text-black/70 dark:text-white/70">
                      성공 {formatDateTime(s.last_success_at)}
                    </div>
                    <div
                      className={
                        failing ? "text-red-600" : "text-black/50 dark:text-white/50"
                      }
                    >
                      실패 {formatDateTime(s.last_failure_at)}
                    </div>
                    {failing && s.last_error_message && (
                      <div
                        className="truncate text-red-600"
                        title={s.last_error_message}
                      >
                        {s.last_error_message}
                      </div>
                    )}
                  </td>
                  <td className="p-2 align-top">
                    {s.is_active ? (
                      <span className="text-green-700 dark:text-green-400">활성</span>
                    ) : (
                      <span className="text-black/40 dark:text-white/40">비활성</span>
                    )}
                  </td>
                  <td className="p-2 align-top">
                    <div className="flex flex-wrap items-start gap-1">
                      <form action={toggleSource}>
                        <input type="hidden" name="id" value={s.id} />
                        <input
                          type="hidden"
                          name="activate"
                          value={String(!s.is_active)}
                        />
                        <button type="submit" className={smallButtonClass}>
                          {s.is_active ? "비활성화" : "활성화"}
                        </button>
                      </form>
                      <Link
                        href={`/admin/sources?edit=${s.id}`}
                        className={smallButtonClass}
                      >
                        편집
                      </Link>
                      <DeleteSourceButton
                        id={s.id}
                        name={s.name}
                        rawCount={rawCount}
                        isActive={s.is_active}
                      />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
