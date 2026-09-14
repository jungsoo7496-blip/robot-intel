"use server";

import { revalidatePath } from "next/cache";

import { requireOperator } from "@/lib/auth";
import { createServiceRoleClient } from "@/lib/supabase/server";

import {
  ALT_TERM_MAX_LENGTH,
  DISPLAY_MAX,
  DISPLAY_MIN,
  INTERVAL_MAX,
  INTERVAL_MIN,
  MAX_ALT_TERMS,
  MAX_KEYWORDS,
  NOTE_MAX_LENGTH,
  PRIORITY_MAX,
  PRIORITY_MIN,
  SCOPE_LABELS,
  TARGET_INFO,
  TERM_MAX_LENGTH,
  isKeywordScope,
  isMissingTableError,
  isNewsTargetKey,
  normalizeAltTerms,
  scopeUsesNews,
  scopeUsesReports,
  type KeywordScope,
} from "./keyword-fields";

/**
 * 검색 키워드 화면 서버 액션 (요구 1-1·1-2·2-1·2-2).
 *
 * 이 화면은 표 2개(search_keywords · news_search_targets)만 고친다.
 * sources 행(실제 수집원)을 만드는 것은 수집 배치의 sync_news_search_sources
 * 하나뿐이다 — 화면과 배치가 각자 주소를 만들면 서로 어긋나기 때문.
 * 그래서 모든 안내 문구가 "다음 수집부터 반영"이라고 말한다.
 */

export type KeywordActionState = { ok: boolean; message: string } | null;

const PAGE_PATH = "/admin/search-keywords";
/** 뉴스 수집 배치 시각 (.github/workflows/collect.yml). */
const NEWS_RUN_HINT = "하루 3회 — 한국시각 06:20·14:20·22:10 예정";
/** 보고서 수집 배치 시각 (.github/workflows/collect-reports.yml, cron 35 21 UTC). */
const REPORT_RUN_HINT = "하루 1회 — 한국시각 06:35 예정";

type Supabase = ReturnType<typeof createServiceRoleClient>;
type DbError = { code?: string; message: string };

function fail(message: string): KeywordActionState {
  return { ok: false, message };
}

const SETUP_MESSAGE =
  "검색 키워드 표가 아직 데이터베이스에 없습니다. 새 마이그레이션이 적용된 뒤에 쓸 수 있습니다.";

function dbErrorMessage(error: DbError): string {
  if (isMissingTableError(error)) return SETUP_MESSAGE;
  if (error.code === "23505") return "이미 등록된 검색어입니다.";
  if (error.code === "23514") {
    return "허용되지 않는 값이 있습니다. 입력을 다시 확인해 주세요.";
  }
  return `저장에 실패했습니다: ${error.message}`;
}

/** 운영 조치 이력 (tasks §14.2). 기록 실패가 조치 자체를 되돌리지는 않는다. */
async function logEvent(
  supabase: Supabase,
  eventType: string,
  targetTable: string,
  targetId: string | null,
  actorId: string,
  reason: string,
) {
  const { error } = await supabase.from("operation_events").insert({
    event_type: eventType,
    target_table: targetTable,
    target_id: targetId,
    actor_id: actorId,
    reason: reason.slice(0, 500),
  });
  if (error) {
    console.error(
      `[admin/search-keywords] operation_events 기록 실패 (${eventType}):`,
      error.message,
    );
  }
}

function str(formData: FormData, key: string, max: number): string {
  return String(formData.get(key) ?? "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, max);
}

function int(formData: FormData, key: string): number {
  return Number.parseInt(String(formData.get(key) ?? ""), 10);
}

// ------------------------------------------------------------
// 검색어 추가 (요구 2-2 — 넣으면 뉴스·보고서에 함께 적용)
// ------------------------------------------------------------

/** 새 검색어가 어디서부터 언제 반영되는지 — 실제 배치 동작 그대로. */
function appliedFromHint(scope: KeywordScope): string {
  const parts: string[] = [];
  if (scopeUsesNews(scope)) {
    parts.push(`네이버·구글 뉴스는 다음 정기 수집(${NEWS_RUN_HINT})부터`);
  }
  if (scopeUsesReports(scope)) {
    parts.push(`보고서 수집원은 다음 보고서 수집(${REPORT_RUN_HINT})부터`);
  }
  return `${parts.join(", ")} 이 검색어로 함께 찾습니다.`;
}

export async function addSearchKeyword(
  _prev: KeywordActionState,
  formData: FormData,
): Promise<KeywordActionState> {
  const profile = await requireOperator();

  // 검색어는 자르지 않고 그대로 받아 길이를 검사한다 — 조용히 잘리면
  // 운영자가 넣은 것과 다른 말로 수집하게 된다.
  const term = str(formData, "term", 500);
  const scopeRaw = String(formData.get("scope") ?? "ALL");
  const note = str(formData, "note", NOTE_MAX_LENGTH);
  const altTerms = normalizeAltTerms(
    String(formData.get("alt_terms") ?? "").slice(0, 500),
  );

  if (!term) return fail("검색어를 입력해 주세요.");
  if (term.length > TERM_MAX_LENGTH) {
    return fail(`검색어는 ${TERM_MAX_LENGTH}자 이내로 입력해 주세요.`);
  }
  if (term.includes('"')) {
    return fail(
      "검색어에 큰따옴표(\")는 넣지 마세요. 같은 뜻 다른 표기는 아래 칸에 쉼표로 나눠 적어 주세요.",
    );
  }
  if (!isKeywordScope(scopeRaw)) {
    return fail("‘쓰는 곳’을 목록에서 골라 주세요.");
  }
  const scope: KeywordScope = scopeRaw;
  if (altTerms.length > MAX_ALT_TERMS) {
    return fail(`같은 뜻 다른 표기는 ${MAX_ALT_TERMS}개까지 넣을 수 있습니다.`);
  }
  const tooLong = altTerms.find((t) => t.length > ALT_TERM_MAX_LENGTH);
  if (tooLong) {
    return fail(
      `같은 뜻 다른 표기는 하나에 ${ALT_TERM_MAX_LENGTH}자 이내로 적어 주세요: ${tooLong}`,
    );
  }
  if (altTerms.some((t) => t.includes('"'))) {
    return fail("같은 뜻 다른 표기에 큰따옴표(\")는 넣을 수 없습니다.");
  }
  const sameAsTerm = altTerms.find(
    (t) => t.toLocaleLowerCase() === term.toLocaleLowerCase(),
  );
  if (sameAsTerm) {
    return fail(
      `‘${sameAsTerm}’은(는) 검색어와 같습니다. 다른 표기만 적어 주세요.`,
    );
  }

  const supabase = createServiceRoleClient();

  // 개수 상한 — 검색어 1개가 뉴스 수집원 2개·보고서 조합 7개로 곱해진다
  const { data: existing, error: listError } = await supabase
    .from("search_keywords")
    .select("id, term");
  if (listError) return fail(dbErrorMessage(listError));
  const rows = existing ?? [];
  if (rows.length >= MAX_KEYWORDS) {
    return fail(
      `검색어는 ${MAX_KEYWORDS}개까지만 등록할 수 있습니다. 쓰지 않는 검색어를 먼저 지워 주세요.`,
    );
  }
  const dup = rows.find(
    (r: { term: string }) =>
      r.term.trim().toLocaleLowerCase() === term.toLocaleLowerCase(),
  );
  if (dup) return fail(`이미 등록된 검색어입니다: ${dup.term}`);

  // 정렬값은 10단위로 뒤에 붙인다 (시드 9행이 10~90을 쓴다)
  const { data: last } = await supabase
    .from("search_keywords")
    .select("sort_order")
    .order("sort_order", { ascending: false })
    .limit(1)
    .maybeSingle();
  const sortOrder = Math.min(
    ((last?.sort_order as number | undefined) ?? 0) + 10,
    9999,
  );

  const { data: inserted, error } = await supabase
    .from("search_keywords")
    .insert({
      term,
      alt_terms: altTerms,
      scope,
      sort_order: sortOrder,
      note: note || null,
    })
    .select("id")
    .single();
  if (error || !inserted) {
    return fail(error ? dbErrorMessage(error) : "추가에 실패했습니다.");
  }

  await logEvent(
    supabase,
    "SEARCH_KEYWORD_CREATE",
    "search_keywords",
    inserted.id,
    profile.id,
    `검색어 추가 — ${term}${
      altTerms.length ? ` (같은 뜻: ${altTerms.join(", ")})` : ""
    } · 쓰는 곳 ${SCOPE_LABELS[scope]}`,
  );
  revalidatePath(PAGE_PATH);
  revalidatePath("/admin/sources");
  return {
    ok: true,
    message: `‘${term}’을(를) 추가했습니다. ${appliedFromHint(scope)}`,
  };
}

// ------------------------------------------------------------
// 검색어 삭제 (요구 3-1과 같은 원칙 — 켜고 끄기 없이 추가·삭제만)
// ------------------------------------------------------------

/**
 * 검색어를 목록에서 지운다.
 *
 * 이미 수집된 기사는 지워지지 않는다. 다음 수집 때 배치가 이 검색어로 만든
 * 수집원 행을 '비활성'으로 돌릴 뿐(삭제하지 않음)이라, 같은 검색어를 다시
 * 넣으면 그 행이 되살아나 수집 이력이 그대로 이어진다.
 */
export async function deleteSearchKeyword(
  _prev: KeywordActionState,
  formData: FormData,
): Promise<KeywordActionState> {
  const profile = await requireOperator();
  const id = String(formData.get("id") ?? "").slice(0, 64);
  if (!id) return fail("삭제할 검색어가 지정되지 않았습니다.");

  const supabase = createServiceRoleClient();
  const { data, error } = await supabase
    .from("search_keywords")
    .delete()
    .eq("id", id)
    .select("term, scope")
    .maybeSingle();
  if (error) return fail(dbErrorMessage(error));
  if (!data) return fail("이미 지워졌거나 찾을 수 없는 검색어입니다.");

  await logEvent(
    supabase,
    "SEARCH_KEYWORD_DELETE",
    "search_keywords",
    id,
    profile.id,
    `검색어 삭제 — ${data.term}`,
  );
  revalidatePath(PAGE_PATH);
  revalidatePath("/admin/sources");
  return {
    ok: true,
    message: `‘${data.term}’을(를) 목록에서 지웠습니다. 이미 수집된 기사는 그대로 남고, 다음 수집부터 이 검색어로는 찾지 않습니다.`,
  };
}

// ------------------------------------------------------------
// 검색 대상 설정 (요구 1-1 — 우선순위·수집 간격을 한꺼번에)
// ------------------------------------------------------------

/**
 * 네이버·구글 대상 설정 1벌을 저장한다. 여기 값이 다음 수집 때 이 대상의
 * 모든 검색어 수집원에 한꺼번에 적용된다 (검색어마다 따로 정하지 않는다).
 */
export async function saveNewsSearchTarget(
  _prev: KeywordActionState,
  formData: FormData,
): Promise<KeywordActionState> {
  const profile = await requireOperator();

  const targetKey = String(formData.get("target_key") ?? "");
  if (!isNewsTargetKey(targetKey)) {
    return fail("검색 대상이 올바르지 않습니다.");
  }
  const info = TARGET_INFO[targetKey];
  const isActive = formData.get("is_active") === "on";
  const priority = int(formData, "priority");
  const interval = int(formData, "fetch_interval_minutes");

  if (!Number.isFinite(priority) || priority < PRIORITY_MIN || priority > PRIORITY_MAX) {
    return fail(
      `우선순위는 ${PRIORITY_MIN}~${PRIORITY_MAX} 사이의 정수여야 합니다 (낮을수록 먼저 수집).`,
    );
  }
  if (!Number.isFinite(interval) || interval < INTERVAL_MIN || interval > INTERVAL_MAX) {
    return fail(`수집 간격은 ${INTERVAL_MIN}~${INTERVAL_MAX}분 사이여야 합니다.`);
  }

  const patch: Record<string, unknown> = {
    is_active: isActive,
    priority,
    fetch_interval_minutes: interval,
    updated_at: new Date().toISOString(),
  };

  // display는 네이버 전용이다 — 구글 카드에는 입력 자체가 없다
  if (info.usesDisplay) {
    const display = int(formData, "display");
    if (!Number.isFinite(display) || display < DISPLAY_MIN || display > DISPLAY_MAX) {
      return fail(`조회 건수는 ${DISPLAY_MIN}~${DISPLAY_MAX} 사이여야 합니다.`);
    }
    patch.display = display;
  }

  const supabase = createServiceRoleClient();
  const { data, error } = await supabase
    .from("news_search_targets")
    .update(patch)
    .eq("target_key", targetKey)
    .select("target_key")
    .maybeSingle();
  if (error) return fail(dbErrorMessage(error));
  if (!data) {
    return fail(
      `‘${info.title}’ 설정을 찾을 수 없습니다. 마이그레이션이 적용됐는지 확인해 주세요.`,
    );
  }

  await logEvent(
    supabase,
    "NEWS_SEARCH_TARGET_UPDATE",
    "news_search_targets",
    null,
    profile.id,
    `${info.title} — ${isActive ? "사용함" : "사용 안 함"} · 우선순위 ${priority} · 간격 ${interval}분${
      info.usesDisplay ? ` · 조회 ${patch.display}건` : ""
    }`,
  );
  revalidatePath(PAGE_PATH);
  revalidatePath("/admin/sources");
  return {
    ok: true,
    message: isActive
      ? `‘${info.title}’ 설정을 저장했습니다. 다음 정기 수집(${NEWS_RUN_HINT})부터 모든 검색어에 함께 적용됩니다.`
      : `‘${info.title}’을(를) 사용하지 않도록 저장했습니다. 다음 수집부터 이 대상에서는 찾지 않습니다 (이미 수집된 기사는 그대로 남습니다).`,
  };
}
