"use server";

import { revalidatePath } from "next/cache";

import { requireOperator } from "@/lib/auth";
import { dispatchWorkflow, isWorkflowBusy } from "@/lib/github-workflow";
import { createServiceRoleClient } from "@/lib/supabase/server";

import { modelLabel } from "./usage/labels";
import {
  ANALYZE_BOTH,
  kstHour,
  MANUAL_COUNT_MAX,
  MANUAL_COUNT_MIN,
  MANUAL_RUN_DAILY_LIMIT,
  quotaAllowance,
  quotaDatePt,
  settingNumber,
  type QuotaSettings,
} from "./usage/quota-math";

/** 운영 조치 이력 기록 (tasks §14.2). */
async function logEvent(
  supabase: ReturnType<typeof createServiceRoleClient>,
  eventType: string,
  targetTable: string,
  targetId: string,
  actorId: string,
  reason?: string,
) {
  await supabase.from("operation_events").insert({
    event_type: eventType,
    target_table: targetTable,
    target_id: targetId,
    actor_id: actorId,
    reason: reason ?? null,
  });
}

/** 수집원 활성화·비활성화 (FR-002). */
export async function toggleSource(formData: FormData) {
  const profile = await requireOperator();
  const id = String(formData.get("id"));
  const activate = String(formData.get("activate")) === "true";

  const supabase = createServiceRoleClient();
  await supabase.from("sources").update({ is_active: activate }).eq("id", id);
  await logEvent(
    supabase,
    activate ? "SOURCE_ENABLE" : "SOURCE_DISABLE",
    "sources",
    id,
    profile.id,
  );
  revalidatePath("/admin/sources");
}

/** 오류 신고 처리: 확인 완료 / 기각 (FR-014). */
export async function resolveReport(formData: FormData) {
  const profile = await requireOperator();
  const id = String(formData.get("id"));
  const dismiss = String(formData.get("dismiss")) === "true";
  const note = String(formData.get("note") ?? "").slice(0, 1000);

  const supabase = createServiceRoleClient();
  await supabase
    .from("error_reports")
    .update({
      status: dismiss ? "DISMISSED" : "RESOLVED",
      operator_note: note || null,
      resolved_by: profile.id,
      resolved_at: new Date().toISOString(),
    })
    .eq("id", id);
  await logEvent(
    supabase,
    dismiss ? "REPORT_DISMISS" : "REPORT_RESOLVE",
    "error_reports",
    id,
    profile.id,
    note,
  );
  revalidatePath("/admin/error-reports");
}

/** 콘텐츠 숨김·재노출 (FR-014). 숨기면 목록·검색에서 즉시 제외된다. */
export async function toggleItemVisibility(formData: FormData) {
  const profile = await requireOperator();
  const id = String(formData.get("id"));
  const hide = String(formData.get("hide")) === "true";
  const reason = String(formData.get("reason") ?? "").slice(0, 500);

  const supabase = createServiceRoleClient();
  await supabase
    .from("published_items")
    .update({
      is_visible: !hide,
      hidden_reason: hide ? reason || "운영자 숨김" : null,
    })
    .eq("id", id);
  await logEvent(
    supabase,
    hide ? "CONTENT_HIDE" : "CONTENT_UNHIDE",
    "published_items",
    id,
    profile.id,
    reason,
  );
  revalidatePath("/admin/error-reports");
  revalidatePath("/trends");
}

/** 운영 액션이 화면(useActionState)에 돌려주는 결과 — 던지지 않고 문장으로. */
export type ActionResult = { ok: boolean; message: string };

/**
 * AI 재분석 요청 (FR-014): 기존 분석은 보존, 큐에 작업을 다시 올린다.
 *
 * 보관 파일(금고)로 옮겨진 카드(vaulted_at)는 거부한다 (계약 C12) — analyses 와
 * 끝난 analysis_jobs 가 DB에서 지워져 배치가 처리할 수 없고, 처리해도 금고 파일과
 * 어긋난다. 거절 이유는 던지지 않고 {ok:false, message} 로 돌려준다 (triggerAnalyzeNow
 * 와 같은 이유 — 던지면 운영자가 사유를 영영 못 본다). 화면은 버튼도 숨긴다.
 */
export async function requestReanalysis(
  _prev: ActionResult | null,
  formData: FormData,
): Promise<ActionResult> {
  const profile = await requireOperator();
  const publishedItemId = String(formData.get("published_item_id") ?? "");
  if (!publishedItemId) {
    return { ok: false, message: "대상 기사가 지정되지 않았습니다." };
  }

  const supabase = createServiceRoleClient();
  const { data: item, error: itemError } = await supabase
    .from("published_items")
    .select("cluster_id, vaulted_at")
    .eq("id", publishedItemId)
    .maybeSingle();
  if (itemError) {
    return {
      ok: false,
      message: `기사 조회에 실패했습니다: ${itemError.message}`,
    };
  }
  if (!item) {
    return {
      ok: false,
      message: "대상 기사를 찾을 수 없습니다. 화면을 새로 고쳐 주세요.",
    };
  }
  if (item.vaulted_at) {
    return {
      ok: false,
      message: "보관 파일로 옮긴 기사는 재분석할 수 없습니다.",
    };
  }

  const { error: jobError } = await supabase
    .from("analysis_jobs")
    .update({
      status: "PENDING",
      available_at: new Date().toISOString(),
      locked_at: null,
      locked_by: null,
      priority: 5, // 일반 기사보다 먼저 처리
      last_error_code: null,
      last_error_message: null,
    })
    .eq("cluster_id", item.cluster_id)
    .eq("job_type", "ARTICLE");
  if (jobError) {
    return {
      ok: false,
      message: `재분석 요청 저장에 실패했습니다: ${jobError.message}`,
    };
  }
  await logEvent(
    supabase,
    "REANALYZE",
    "published_items",
    publishedItemId,
    profile.id,
    "운영자 재분석 요청 — 다음 분석 배치에서 처리",
  );
  revalidatePath("/admin/error-reports");
  return {
    ok: true,
    message: "재분석을 요청했습니다. 다음 분석 배치에서 처리됩니다.",
  };
}

// 수동 URL 등록(submitManualUrl)은 2026-09-08에 제거했다 (사용자 요구 4 —
// 운영 1년 동안 등록 0건). 화면(/admin/manual-items)과 함께 지웠고,
// 여기서만 쓰던 헬퍼(normalizeUrl·rejectNonPublicUrl·TRACKING_PARAMS)도 함께
// 정리했다. 수집원 화면(admin/sources/actions.ts)은 자기 rejectNonPublicUrl을
// 따로 갖고 있으므로 영향이 없다. manual_submissions 표(0건)와
// raw_items.manual_submission_id는 그대로 둔다 — publish.py가 아직 읽는다.

// ------------------------------------------------------------
// 수동 배치 실행 (사용자 요청 2026-08-08): GitHub Actions workflow_dispatch.
// Vercel 서버에서 배치를 직접 못 돌리므로, 정기 배치와 동일한 워크플로를
// 즉시 트리거한다 (보통 30초~2분 내 시작).
// 실제 호출은 @/lib/github-workflow 가 담당한다 — 이 파일은 "use server"라
// 여기서 export하면 requireOperator를 안 거치는 액션 엔드포인트가 되므로.
// ------------------------------------------------------------

/** 지금 수집 실행 — 정기 수집과 동일한 collect 워크플로를 즉시 트리거. */
export async function triggerCollectNow() {
  const profile = await requireOperator();
  await dispatchWorkflow("collect.yml");

  const supabase = createServiceRoleClient();
  await supabase.from("operation_events").insert({
    event_type: "OTHER",
    target_table: "sources",
    actor_id: profile.id,
    reason: "수동 수집 실행 (workflow_dispatch)",
  });
  revalidatePath("/admin/sources");
}

// ------------------------------------------------------------
// 강제 분석 — 화면(admin/usage/run-analyze.tsx)과 규칙을 공유한다.
// 남은 여유·시간 예산·막차 소진 계산은 admin/usage/quota-math.ts 하나뿐이고
// 화면도 같은 함수를 쓴다. 규칙을 양쪽에 복사해 두면 화면은 고를 수 있다고
// 하는데 서버는 거절하는 상태가 다시 생긴다.
//
// 예상된 거절은 던지지 않고 { ok, message }로 돌려준다 — 서버 액션이 던진
// 오류는 전역 오류 화면(src/app/error.tsx)으로 가는데 그 화면은 메시지를
// 보여주지 않고, 프로덕션 빌드는 메시지를 다이제스트로 바꾼다. 즉 운영자는
// 이유를 영영 못 본다. (next/dist/docs/01-app/01-getting-started/10-error-handling.md)
// ------------------------------------------------------------

/** 강제 분석 폼(useActionState)에 돌려주는 결과 — ActionResult 와 같은 모양. */
export type AnalyzeRunResult = ActionResult;

function analyzeFail(message: string): AnalyzeRunResult {
  return { ok: false, message };
}

// 배치 기본값 (scripts/kiro_batch/config.py). app_settings에 같은 키가 있으면
// 그쪽이 이긴다 — 배치와 같은 순서다.
const DEFAULT_ARTICLE_MODELS = "gemini-flash-lite-latest,gemini-3.1-flash-lite";
const SETTING_FALLBACKS = {
  dailySoftLimit: 480,
  briefDailyReserve: 5,
  morningReserveCalls: 150,
  batchMaxCount: 400,
  batchMaxSeconds: 420,
  targetRpm: 12,
  streamWorkers: 2,
};

/**
 * 수동 실행을 막아야 하는 이유 (없으면 null).
 *
 * 2026-08-11: 8/8~8/11 실측으로 analyze가 236회 수동 실행됐고(222회는 동시
 * 실행이라 즉시 취소) 8/11 하루에만 190분을 썼다 — GitHub Actions 월 한도
 * 2,000분을 이 하나로 넘길 페이스였다. 막아야 하는 것은 '동시 실행'과
 * 'Actions 월 한도'이지 경과 시간 그 자체가 아니므로, 쿨다운 대신 하루
 * 상한만 두고 동시 실행은 isWorkflowBusy가 GitHub에 직접 물어 판정한다.
 */
async function manualRunBlockReason(
  supabase: ReturnType<typeof createServiceRoleClient>,
  reasonPrefix: string,
  workflow: string,
): Promise<string | null> {
  const since = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
  const { data } = await supabase
    .from("operation_events")
    .select("created_at")
    .eq("target_table", "analysis_jobs")
    .like("reason", `${reasonPrefix}%`)
    .gte("created_at", since)
    .order("created_at", { ascending: false });

  if ((data ?? []).length >= MANUAL_RUN_DAILY_LIMIT) {
    return (
      `수동 분석은 24시간에 ${MANUAL_RUN_DAILY_LIMIT}회까지만 가능합니다. ` +
      `정기 배치도 하루 4회 자동으로 돌며, GitHub 실행 시간이 월 한도가 있어 ` +
      `무제한으로 돌릴 수 없습니다.`
    );
  }
  if (await isWorkflowBusy(workflow)) {
    return (
      `분석 배치가 아직 실행 중입니다. 끝난 뒤 다시 누르세요 — 지금 누르면 ` +
      `뒤 실행이 앞 실행을 기다리다 취소되고, 대기 건수는 줄지 않은 채 ` +
      `실행 시간만 소모됩니다. (아래 ‘배치 실행 이력’에서 ‘실행 중’인지 볼 수 있습니다)`
    );
  }
  return null;
}

type AnalyzeContext = {
  /** 분석에 쓰는 모델 목록 (app_settings 순서 그대로). */
  articleModels: string[];
  settings: QuotaSettings;
};

/** app_settings에서 분석 관련 설정을 읽는다. 조회 실패면 null. */
async function loadAnalyzeContext(
  supabase: ReturnType<typeof createServiceRoleClient>,
): Promise<AnalyzeContext | null> {
  const { data, error } = await supabase
    .from("app_settings")
    .select("key, value")
    .in("key", [
      "article_models",
      "article_daily_soft_limit",
      "brief_daily_reserve",
      "morning_reserve_calls",
      "article_batch_max_count",
      "article_batch_max_seconds",
      "article_target_rpm",
      "article_stream_workers",
    ]);
  if (error) return null;

  const row = new Map<string, unknown>(
    (data ?? []).map((r) => [r.key as string, r.value]),
  );
  const articleModels = String(
    row.get("article_models") ?? DEFAULT_ARTICLE_MODELS,
  )
    .split(",")
    .map((m) => m.trim())
    .filter(Boolean);
  if (articleModels.length === 0) return null;

  return {
    articleModels,
    settings: {
      dailySoftLimit: settingNumber(
        row.get("article_daily_soft_limit"),
        SETTING_FALLBACKS.dailySoftLimit,
      ),
      briefDailyReserve: settingNumber(
        row.get("brief_daily_reserve"),
        SETTING_FALLBACKS.briefDailyReserve,
      ),
      morningReserveCalls: settingNumber(
        row.get("morning_reserve_calls"),
        SETTING_FALLBACKS.morningReserveCalls,
      ),
      batchMaxCount: settingNumber(
        row.get("article_batch_max_count"),
        SETTING_FALLBACKS.batchMaxCount,
      ),
      batchMaxSeconds: settingNumber(
        row.get("article_batch_max_seconds"),
        SETTING_FALLBACKS.batchMaxSeconds,
      ),
      targetRpm: settingNumber(
        row.get("article_target_rpm"),
        SETTING_FALLBACKS.targetRpm,
      ),
      streamWorkers: settingNumber(
        row.get("article_stream_workers"),
        SETTING_FALLBACKS.streamWorkers,
      ),
    },
  };
}

/**
 * 오늘 이 모델들이 쓴 호출 수. 조회에 실패하면 null —
 * 관측 실패가 기능 정지가 되면 안 된다(isWorkflowBusy와 동일).
 */
async function todaysCallsByModel(
  supabase: ReturnType<typeof createServiceRoleClient>,
  models: string[],
  quotaDate: string,
): Promise<number[] | null> {
  // 행 수를 센다 — record_gemini_call은 request_count를 건드리지 않아 항상 1이다.
  // 행을 통째로 받아 세면 PostgREST 기본 상한(1,000행)에 걸려 하루치를 덜 세게 된다.
  const used = await Promise.all(
    models.map(async (m) => {
      const { count, error } = await supabase
        .from("gemini_calls")
        .select("id", { count: "exact", head: true })
        .eq("quota_date_pt", quotaDate)
        .eq("model_name", m);
      return error ? null : (count ?? 0);
    }),
  );
  return used.some((u) => u === null) ? null : (used as number[]);
}

/** 폼의 max_count 검증 — 빈 값이면 null(워크플로에 넘기지 않음). */
function parseManualCount(
  raw: string,
): { ok: true; value: number | null } | { ok: false; message: string } {
  const value = raw.trim();
  if (value === "") return { ok: true, value: null };
  if (!/^\d{1,4}$/.test(value)) {
    return {
      ok: false,
      message:
        "분석 건수는 숫자만 입력하세요. 비우면 시간이 되는 만큼 처리합니다.",
    };
  }
  const n = Number(value);
  if (n < MANUAL_COUNT_MIN || n > MANUAL_COUNT_MAX) {
    return {
      ok: false,
      message:
        `분석 건수는 ${MANUAL_COUNT_MIN}~${MANUAL_COUNT_MAX} 사이로 입력하세요. ` +
        `비우면 시간이 되는 만큼 처리합니다.`,
    };
  }
  return { ok: true, value: n };
}

/**
 * 분석 대기분 강제 분석 — 선택한 모델·건수로 analyze 워크플로를 즉시 트리거.
 * 성공도 실패도 같은 화면(useActionState)에 문장으로 돌려준다.
 */
export async function triggerAnalyzeNow(
  _prev: AnalyzeRunResult | null,
  formData: FormData,
): Promise<AnalyzeRunResult> {
  const profile = await requireOperator();
  const supabase = createServiceRoleClient();
  const context = await loadAnalyzeContext(supabase);

  // 허용 모델은 우리 설정에서 온다 — 화면 드롭다운도 같은 목록을 쓰므로
  // 둘이 어긋날 수 없다. 설정을 못 읽으면 배치 기본값으로 판정한다.
  const allowedModels =
    context?.articleModels ??
    DEFAULT_ARTICLE_MODELS.split(",").map((m) => m.trim());
  const model = String(formData.get("model") ?? "");
  if (model !== ANALYZE_BOTH && !allowedModels.includes(model)) {
    return analyzeFail(
      "허용되지 않은 모델입니다. 화면을 새로 고친 뒤 다시 골라 주세요.",
    );
  }

  const parsed = parseManualCount(String(formData.get("max_count") ?? ""));
  if (!parsed.ok) return analyzeFail(parsed.message);
  const maxCount = parsed.value;

  const blocked = await manualRunBlockReason(
    supabase,
    "수동 분석 실행",
    "analyze.yml",
  );
  if (blocked) return analyzeFail(blocked);

  // 남은 쿼터 확인 — 한도를 다 쓴 상태로 돌리면 Actions 시간만 태운다.
  // 건수는 '기사 수'이므로 남은 호출도 기사 수로 바꿔 비교한다
  // (화면 드롭다운이 거르는 기준과 같은 quotaAllowance().articlesLeft).
  const runModels = model === ANALYZE_BOTH ? allowedModels : [model];
  const used = context
    ? await todaysCallsByModel(supabase, runModels, quotaDatePt())
    : null;
  const left =
    context && used ? quotaAllowance(used, context.settings, kstHour()) : null;

  if (left && left.articlesLeft <= 0) {
    return analyzeFail(
      `오늘 Gemini 분석 한도를 다 썼습니다. 지금 실행해도 처리되지 않습니다. ` +
        (left.night
          ? `밤에는 아침 몫을 남겨 두므로, 오전 6시 이후에 다시 눌러 보세요.`
          : `한도는 매일 오후 5시에 새로 시작합니다.`),
    );
  }
  if (left && maxCount !== null && maxCount > left.articlesLeft) {
    return analyzeFail(
      `오늘 남은 분석 여유는 ${left.articlesLeft}건입니다 (요청 ${maxCount}건). ` +
        `${left.articlesLeft} 이하로 고르거나 비워 두세요.` +
        (left.night ? ` 밤에는 아침 몫을 남겨 둡니다.` : ``),
    );
  }

  // model을 비워 보내면 배치가 기본값(2모델 병렬)으로 돈다.
  // max_count도 비우면 시간 예산이 허락하는 만큼 처리한다 (워크플로 기본값 = 빈 값).
  const inputs: Record<string, string> = {};
  if (model !== ANALYZE_BOTH) inputs.model = model;
  if (maxCount !== null) inputs.max_count = String(maxCount);
  try {
    await dispatchWorkflow("analyze.yml", inputs);
  } catch (e) {
    // 토큰 누락·GitHub 오류도 운영자가 읽을 수 있어야 한다
    return analyzeFail(
      `분석 배치 실행 요청에 실패했습니다: ${
        e instanceof Error ? e.message : String(e)
      }`,
    );
  }

  await supabase.from("operation_events").insert({
    event_type: "OTHER",
    target_table: "analysis_jobs",
    actor_id: profile.id,
    // 접두사 "수동 분석 실행"은 manualRunBlockReason의 일일 한도 집계 기준이다
    reason:
      `수동 분석 실행 — ` +
      (model === ANALYZE_BOTH ? "2개 모델 동시" : `${modelLabel(model)} 단독`) +
      (maxCount === null ? "" : ` · 최대 ${maxCount}건`),
  });
  revalidatePath("/admin/usage");
  return {
    ok: true,
    message:
      `분석 배치를 요청했습니다` +
      (maxCount === null ? "" : ` (최대 ${maxCount}건)`) +
      `. 30초~2분 안에 시작하며, 끝나면 아래 ‘배치 실행 이력’에 나타납니다.`,
  };
}

/**
 * 보고서 노출 수동 전환 (보고서 스펙 §30·§32).
 * manual_override를 켜서 이후 자동 재분석이 노출 상태를 덮지 않게 한다.
 */
export async function toggleReportVisibility(formData: FormData) {
  const profile = await requireOperator();
  const id = String(formData.get("id"));
  const show = String(formData.get("show")) === "true";

  const supabase = createServiceRoleClient();
  await supabase
    .from("report_documents")
    .update({ is_visible: show, manual_override: true })
    .eq("id", id);
  await logEvent(
    supabase,
    "OTHER",
    "report_documents",
    id,
    profile.id,
    show ? "보고서 수동 공개" : "보고서 수동 비공개",
  );
  revalidatePath("/admin/reports");
  revalidatePath("/reports");
}
