import Link from "next/link";

import { createServiceRoleClient } from "@/lib/supabase/server";

import { resolveReport, toggleItemVisibility } from "../actions";
import { ReanalyzeButton } from "./reanalyze-button";

export const dynamic = "force-dynamic";

/*
 * 콘텐츠·신고 관리 (2026-09-08 개편 — 운영자 피드백 5).
 *
 * 이전 화면은 '숨겨진 항목 표본 20건 + 최근 20건'만 보여 줘서, 운영자가 특정
 * 기사를 찾아 처리할 수가 없었다(노출 9,701건). 이제는 제목 검색 + 페이지
 * 넘김으로 실제 콘텐츠를 찾아 열어보고 숨기거나 되돌린다.
 *
 * 오류 신고 목록은 0건이어도 **항상** 보여 준다. 이전에는 신고가 있을 때만
 * 나타나서 운영자가 기능 자체를 몰랐다.
 *
 * 숨김 사유 표기 (실측 2026-09-08: 숨김 911건 = 사유 없음 885 + '중복…' 26):
 * dedup_sweep.py는 사유를 남기지 않으므로, 사유가 비었거나 '중복'으로
 * 시작하면 배치의 자동 중복 정리로 표시한다. 이 화면의 '숨기기'는 항상 사유를
 * 남긴다(비워 두면 '운영자 숨김').
 */

const STATUS_LABEL: Record<string, string> = {
  OPEN: "미처리",
  RESOLVED: "확인 완료",
  DISMISSED: "기각",
};

const PAGE_SIZE = 20;
const REPORT_LIMIT = 50;

type Row = {
  id: string;
  title: string | null;
  published_at: string | null;
  representative_source_name: string | null;
  is_visible: boolean;
  hidden_reason: string | null;
  /** 있으면 부속이 보관 파일(금고)로 옮겨져 재분석할 수 없다 (계약 C12) */
  vaulted_at: string | null;
};

/** 오류 신고에 붙어 오는 대상 기사 (published_items 임베드). */
type ReportItem = {
  id: string;
  title: string;
  is_visible: boolean;
  hidden_reason: string | null;
  vaulted_at: string | null;
};

/** 보관 파일로 옮긴 기사 — 버튼 대신 이유를 한 줄로 (계약 C12). */
function VaultedNote() {
  return (
    <span className="text-xs text-black/45 dark:text-white/45">
      보관 파일로 옮김 · 재분석 불가
    </span>
  );
}

type ListState = "visible" | "hidden" | "all";

/** 숨김 사유 표기 — 사유가 비었거나 '중복…'이면 배치의 자동 중복 정리다. */
function hiddenLabel(reason: string | null): string {
  const r = (reason ?? "").trim();
  return r === "" || r.startsWith("중복") ? "자동 중복 정리" : r;
}

function formatDay(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul" });
}

function listHref(state: ListState, q: string, page: number): string {
  const sp = new URLSearchParams();
  if (state !== "visible") sp.set("state", state);
  if (q) sp.set("q", q);
  if (page > 1) sp.set("page", String(page));
  const s = sp.toString();
  return s ? `/admin/error-reports?${s}` : "/admin/error-reports";
}

const smallButtonClass =
  "rounded border border-black/20 px-2 py-1 text-xs hover:bg-black/5 dark:border-white/25 dark:hover:bg-white/10";

const tabClass = (active: boolean) =>
  `rounded border px-2 py-1 text-sm ${
    active
      ? "border-black/70 font-semibold dark:border-white/70"
      : "border-black/15 hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
  }`;

export default async function ContentAdminPage({
  searchParams,
}: {
  searchParams: Promise<{ state?: string; q?: string; page?: string }>;
}) {
  const params = await searchParams;
  const state: ListState =
    params.state === "hidden" || params.state === "all"
      ? params.state
      : "visible";
  const q = (params.q ?? "").trim().slice(0, 100);
  const page = Math.max(1, Number(params.page ?? "1") || 1);

  const supabase = createServiceRoleClient();

  let listQuery = supabase
    .from("published_items")
    .select(
      "id, title, published_at, representative_source_name, is_visible, hidden_reason, vaulted_at",
      { count: "exact" },
    );
  if (state !== "all") listQuery = listQuery.eq("is_visible", state === "visible");
  if (q) {
    // LIKE 와일드카드(%, _)는 글자 그대로 찾도록 이스케이프 (src/lib/data.ts와 동일)
    listQuery = listQuery.ilike("title", `%${q.replace(/[%_]/g, (m) => `\\${m}`)}%`);
  }

  const [listRes, visibleCountRes, hiddenCountRes, reportsRes] = await Promise.all([
    listQuery
      .order("published_at", { ascending: false })
      .range((page - 1) * PAGE_SIZE, page * PAGE_SIZE - 1),
    supabase
      .from("published_items")
      .select("id", { count: "exact", head: true })
      .eq("is_visible", true),
    supabase
      .from("published_items")
      .select("id", { count: "exact", head: true })
      .eq("is_visible", false),
    supabase
      .from("error_reports")
      .select("*, published_items(id, title, is_visible, hidden_reason, vaulted_at)")
      .order("created_at", { ascending: false })
      .limit(REPORT_LIMIT),
  ]);

  const rows = (listRes.data ?? []) as Row[];
  const total = listRes.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const visibleCount = visibleCountRes.count ?? 0;
  const hiddenCount = hiddenCountRes.count ?? 0;
  const reports = reportsRes.data ?? [];
  const openReports = reports.filter((r) => r.status === "OPEN").length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">콘텐츠·신고 관리</h1>
        <Link
          href="/admin"
          className="rounded border border-black/15 px-2 py-1 text-sm hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
        >
          ← 운영 현황
        </Link>
      </div>
      <p className="text-sm text-black/60 dark:text-white/60">
        기사를 찾아 열어보고, 잘못된 것은 숨기거나 AI 재분석을 맡기는 화면입니다.{" "}
        <strong>숨기기는 삭제가 아닙니다</strong> — 목록·검색에서만 빠지고, 언제든
        &lsquo;다시 보이기&rsquo;로 되돌릴 수 있습니다.
      </p>

      {/* --- 독자 오류 신고: 0건이어도 항상 보여 준다 (운영자 피드백 5-ii) --- */}
      <section className="space-y-3 rounded-lg border border-black/10 p-4 dark:border-white/15">
        <div>
          <h2 className="font-semibold">
            독자 오류 신고{" "}
            <span className="text-sm font-normal text-black/50 dark:text-white/50">
              미처리 {openReports}건
            </span>
          </h2>
          <p className="mt-1 text-sm text-black/60 dark:text-white/60">
            기사 화면 맨 아래 &lsquo;오류 신고&rsquo; 버튼으로 독자가 보낸
            내용입니다.
          </p>
        </div>

        {reports.length === 0 ? (
          <p className="rounded border border-dashed border-black/20 p-6 text-center text-sm text-black/50 dark:border-white/25 dark:text-white/50">
            접수된 신고가 없습니다.
          </p>
        ) : (
          reports.map((r) => {
            const item = r.published_items as ReportItem | null;
            return (
              <div
                key={r.id}
                className="rounded-lg border border-black/10 bg-background p-4 text-sm dark:border-white/15"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                      r.status === "OPEN"
                        ? "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200"
                        : "bg-black/5 text-black/60 dark:bg-white/10 dark:text-white/60"
                    }`}
                  >
                    {STATUS_LABEL[r.status] ?? r.status}
                  </span>
                  <span className="font-medium">{r.report_type}</span>
                  <span className="text-black/40 dark:text-white/40">
                    {new Date(r.created_at).toLocaleString("ko-KR", {
                      timeZone: "Asia/Seoul",
                    })}
                  </span>
                </div>

                {item && (
                  <p className="mt-1">
                    대상:{" "}
                    <a
                      href={`/trends/${item.id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline"
                    >
                      {item.title}
                    </a>
                    {!item.is_visible && (
                      <span className="ml-2 text-xs text-amber-700 dark:text-amber-400">
                        (숨김 상태: {hiddenLabel(item.hidden_reason)})
                      </span>
                    )}
                  </p>
                )}
                {r.description && (
                  <p className="mt-1 text-black/70 dark:text-white/70">
                    “{r.description}”
                  </p>
                )}
                {r.operator_note && (
                  <p className="mt-1 text-xs text-black/50 dark:text-white/50">
                    운영자 메모: {r.operator_note}
                  </p>
                )}

                <div className="mt-3 flex flex-wrap gap-2">
                  {r.status === "OPEN" && (
                    <>
                      <form action={resolveReport}>
                        <input type="hidden" name="id" value={r.id} />
                        <input type="hidden" name="dismiss" value="false" />
                        <button className={smallButtonClass}>확인 완료</button>
                      </form>
                      <form action={resolveReport}>
                        <input type="hidden" name="id" value={r.id} />
                        <input type="hidden" name="dismiss" value="true" />
                        <button className={smallButtonClass}>기각</button>
                      </form>
                    </>
                  )}
                  {item && (
                    <>
                      <form action={toggleItemVisibility}>
                        <input type="hidden" name="id" value={item.id} />
                        <input
                          type="hidden"
                          name="hide"
                          value={String(item.is_visible)}
                        />
                        <input
                          type="hidden"
                          name="reason"
                          value={`오류 신고(${r.report_type}) 처리`}
                        />
                        <button className={smallButtonClass}>
                          {item.is_visible ? "숨기기" : "다시 보이기"}
                        </button>
                      </form>
                      {item.vaulted_at ? (
                        <VaultedNote />
                      ) : (
                        <ReanalyzeButton
                          publishedItemId={item.id}
                          className={smallButtonClass}
                        />
                      )}
                    </>
                  )}
                </div>
              </div>
            );
          })
        )}
      </section>

      {/* --- 게시된 콘텐츠 찾아보기 --- */}
      <section className="space-y-3 rounded-lg border border-black/10 p-4 dark:border-white/15">
        <h2 className="font-semibold">게시된 콘텐츠</h2>

        <div className="flex flex-wrap items-center gap-2">
          <Link href={listHref("visible", q, 1)} className={tabClass(state === "visible")}>
            노출 중 {visibleCount}건
          </Link>
          <Link href={listHref("hidden", q, 1)} className={tabClass(state === "hidden")}>
            숨김 {hiddenCount}건
          </Link>
          <Link href={listHref("all", q, 1)} className={tabClass(state === "all")}>
            전체
          </Link>
        </div>

        <form method="get" className="flex flex-wrap items-center gap-2">
          {state !== "visible" && <input type="hidden" name="state" value={state} />}
          <input
            type="text"
            name="q"
            defaultValue={q}
            aria-label="제목으로 찾기"
            placeholder="제목으로 찾기"
            className="w-64 rounded border border-black/20 bg-transparent px-2 py-1 text-sm dark:border-white/25"
          />
          <button className={smallButtonClass}>찾기</button>
          {q && (
            <Link
              href={listHref(state, "", 1)}
              className="text-xs text-black/50 underline dark:text-white/50"
            >
              검색 지우기
            </Link>
          )}
        </form>

        {state === "hidden" && (
          <p className="text-sm text-black/60 dark:text-white/60">
            사유가 &lsquo;자동 중복 정리&rsquo;인 것은 같은 사건을 여러 매체가 쓴
            기사를 배치가 하나만 남긴 결과입니다 — 정상 동작입니다.
          </p>
        )}

        {listRes.error ? (
          <p className="rounded border border-dashed border-black/20 p-6 text-center text-sm text-black/50 dark:border-white/25 dark:text-white/50">
            목록을 불러오지 못했습니다. 잠시 뒤 새로고침해 주세요.
          </p>
        ) : rows.length === 0 ? (
          <p className="rounded border border-dashed border-black/20 p-6 text-center text-sm text-black/50 dark:border-white/25 dark:text-white/50">
            {q ? `‘${q}’에 해당하는 기사가 없습니다.` : "해당하는 기사가 없습니다."}
          </p>
        ) : (
          <>
            <p className="text-xs text-black/50 dark:text-white/50">
              {total}건 중 {(page - 1) * PAGE_SIZE + 1}–
              {(page - 1) * PAGE_SIZE + rows.length}번째
            </p>
            <ul className="space-y-2 text-sm">
              {rows.map((item) => (
                <li
                  key={item.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded border border-black/10 p-3 dark:border-white/15"
                >
                  <div className="min-w-0">
                    <a
                      href={`/trends/${item.id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline"
                    >
                      {item.title ?? "(제목 없음)"}
                    </a>
                    <div className="text-xs text-black/50 dark:text-white/50">
                      {formatDay(item.published_at)}
                      {item.representative_source_name &&
                        ` · ${item.representative_source_name}`}
                      {!item.is_visible && ` · 숨김: ${hiddenLabel(item.hidden_reason)}`}
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    {item.is_visible ? (
                      <form action={toggleItemVisibility} className="flex gap-1">
                        <input type="hidden" name="id" value={item.id} />
                        <input type="hidden" name="hide" value="true" />
                        <input
                          type="text"
                          name="reason"
                          maxLength={100}
                          aria-label="숨김 사유"
                          placeholder="숨김 사유"
                          className="w-28 rounded border border-black/20 bg-transparent px-2 py-1 text-xs dark:border-white/25"
                        />
                        <button className={smallButtonClass}>숨기기</button>
                      </form>
                    ) : (
                      <form action={toggleItemVisibility}>
                        <input type="hidden" name="id" value={item.id} />
                        <input type="hidden" name="hide" value="false" />
                        <button className={smallButtonClass}>다시 보이기</button>
                      </form>
                    )}
                    {item.vaulted_at ? (
                      <VaultedNote />
                    ) : (
                      <ReanalyzeButton
                        publishedItemId={item.id}
                        className={smallButtonClass}
                      />
                    )}
                  </div>
                </li>
              ))}
            </ul>

            {totalPages > 1 && (
              <div className="flex items-center gap-3 text-sm">
                {page > 1 ? (
                  <Link href={listHref(state, q, page - 1)} className="underline">
                    ← 이전
                  </Link>
                ) : (
                  <span className="text-black/30 dark:text-white/30">← 이전</span>
                )}
                <span className="text-black/50 dark:text-white/50">
                  {page} / {totalPages}
                </span>
                {page < totalPages ? (
                  <Link href={listHref(state, q, page + 1)} className="underline">
                    다음 →
                  </Link>
                ) : (
                  <span className="text-black/30 dark:text-white/30">다음 →</span>
                )}
              </div>
            )}
          </>
        )}
        <p className="text-xs text-black/50 dark:text-white/50">
          AI 재분석은 기존 분석을 지우지 않고, 다음 분석 배치에서 새 분석으로 바꿔
          놓습니다. 오래되어 보관 파일로 옮긴 기사는 분석 원본이 DB에 없어 재분석
          버튼이 나타나지 않습니다.
        </p>
      </section>
    </div>
  );
}
