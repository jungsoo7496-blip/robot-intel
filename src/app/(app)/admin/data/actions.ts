"use server";

import { revalidatePath } from "next/cache";

import { requireOperator } from "@/lib/auth";
import { createServiceRoleClient } from "@/lib/supabase/server";

import { dispatchWorkflow, isWorkflowBusy } from "@/lib/github-workflow";

export type ActionResult = { ok: boolean; message: string };

// ------------------------------------------------------------
// 보존 정책 (app_settings) — 배치 config.py가 같은 키를 읽는다.
// 값은 jsonb 그대로: 일수는 숫자, 보존 여부는 불리언. 0 = 안 함.
// ------------------------------------------------------------
type DaysRule = { kind: "days"; max: number };
type BoolRule = { kind: "bool" };

const RETENTION_RULES: Record<string, DaysRule | BoolRule> = {
  raw_text_retention_days: { kind: "days", max: 365 },
  extract_expire_days: { kind: "days", max: 365 },
  analysis_expire_days: { kind: "days", max: 365 },
  nonrep_body_retention_days: { kind: "days", max: 365 },
  unpublished_retention_days: { kind: "days", max: 365 },
  keep_exclude_body: { kind: "bool" },
  keep_raw_response: { kind: "bool" },
};

// publish.py MERGE_WINDOW_DAYS — 2차 병합 창. 비대표 본문은 이보다 짧게 보존할 수 없다
const MERGE_WINDOW_DAYS = 7;

const RETENTION_DESCRIPTIONS: Record<string, string> = {
  raw_text_retention_days: "원문 raw_text 보존기간(일)",
  extract_expire_days:
    "본문 추출 대기가 발행 후 며칠을 넘기면 EXPIRED로 정리할지 (0=안 함)",
  analysis_expire_days:
    "분석 대기 job이 원문 발행 후 며칠을 넘기면 CANCELLED로 정리할지 (0=안 함)",
  nonrep_body_retention_days:
    "클러스터 대표가 아닌 구성원 본문(clean_text)을 며칠 뒤 비울지 — 2차 병합 창(7일)보다 짧으면 안 됨",
  unpublished_retention_days:
    "게시 안 된 클러스터(로봇 뉴스 아님·병합됨)의 AI 분석 결과와 대표 본문을 며칠 뒤 비울지 (0=안 함) — 제목·링크·수집 기록은 남김",
  keep_exclude_body: "로컬 필터 EXCLUDE 판정 항목의 본문을 보존할지",
  keep_raw_response: "Gemini 응답 원문(analyses.raw_response)을 보존할지",
};

function parseRetentionForm(
  formData: FormData,
): { values: Record<string, number | boolean> } | { error: string } {
  const values: Record<string, number | boolean> = {};
  for (const [key, rule] of Object.entries(RETENTION_RULES)) {
    if (rule.kind === "bool") {
      // 체크박스는 켜졌을 때만 "on"으로 전송된다
      values[key] = formData.get(key) === "on";
      continue;
    }
    const raw = String(formData.get(key) ?? "").trim();
    if (!/^\d{1,4}$/.test(raw)) {
      return { error: `${key}: 0 이상의 정수(일)만 입력할 수 있습니다.` };
    }
    const n = Number(raw);
    if (n > rule.max) {
      return { error: `${key}: ${rule.max}일을 넘길 수 없습니다.` };
    }
    values[key] = n;
  }
  const nonrep = values.nonrep_body_retention_days as number;
  if (nonrep > 0 && nonrep < MERGE_WINDOW_DAYS) {
    return {
      error:
        `비대표 구성원 본문 보존은 2차 병합 창(${MERGE_WINDOW_DAYS}일)보다 짧게 둘 수 없습니다. ` +
        `0(안 함) 또는 ${MERGE_WINDOW_DAYS} 이상으로 입력하세요.`,
    };
  }
  return { values };
}

/** 보존 정책 저장 — 바뀐 키만 app_settings에 쓰고 이력을 남긴다. */
export async function updateRetentionSettings(
  _prev: ActionResult | null,
  formData: FormData,
): Promise<ActionResult> {
  const profile = await requireOperator();
  const parsed = parseRetentionForm(formData);
  if ("error" in parsed) return { ok: false, message: parsed.error };

  const supabase = createServiceRoleClient();
  const keys = Object.keys(RETENTION_RULES);
  const { data: rows, error: readError } = await supabase
    .from("app_settings")
    .select("key, value")
    .in("key", keys);
  if (readError) {
    return { ok: false, message: `현재 설정을 읽지 못했습니다: ${readError.message}` };
  }
  const current = new Map<string, unknown>(
    (rows ?? []).map((r) => [r.key as string, r.value]),
  );

  const changes: string[] = [];
  for (const key of keys) {
    const next = parsed.values[key];
    const existing = current.has(key) ? current.get(key) : undefined;
    // jsonb 값은 그대로 온다 (숫자/불리언). 문자열로 온 옛 값도 비교되게 정규화
    const same =
      existing !== undefined &&
      (typeof next === "boolean"
        ? String(existing) === String(next)
        : Number(existing) === next);
    if (same) continue;

    const write = current.has(key)
      ? supabase.from("app_settings").update({ value: next }).eq("key", key)
      : supabase.from("app_settings").insert({
          key,
          value: next,
          description: RETENTION_DESCRIPTIONS[key] ?? null,
        });
    const { error } = await write;
    if (error) {
      return { ok: false, message: `${key} 저장 실패: ${error.message}` };
    }
    changes.push(
      `${key} ${existing === undefined ? "(없음)" : String(existing)} → ${String(next)}`,
    );
  }

  if (changes.length === 0) {
    return { ok: true, message: "바뀐 값이 없습니다." };
  }

  await supabase.from("operation_events").insert({
    event_type: "DATA_RETENTION_UPDATE",
    target_table: "app_settings",
    actor_id: profile.id,
    reason: `보존 정책 변경: ${changes.join(", ")}`.slice(0, 1000),
  });
  revalidatePath("/admin/data");
  return {
    ok: true,
    message:
      `저장했습니다 (${changes.length}개 변경). 매일 03:47 정리 배치와 다음 수집·분석 ` +
      `배치부터 새 값이 적용됩니다.`,
  };
}

// ------------------------------------------------------------
// 정리 배치 실행 — cleanup.yml workflow_dispatch (tasks 입력 → CLEANUP_TASKS)
// ------------------------------------------------------------
const CLEANUP_PRESETS: Record<string, { tasks: string; label: string }> = {
  daily: { tasks: "", label: "일일 정리" },
  purge_columns: {
    tasks: "null_raw_text_all,null_raw_response",
    label: "일회성 — 복제 컬럼 비우기 (raw_text 전량, PASS 분석 raw_response)",
  },
  purge_backlog: {
    tasks: "expire_extract_backlog,cancel_stale_jobs",
    label: "일회성 — 오래된 대기 정리 (추출 EXPIRED, 분석 CANCELLED)",
  },
};

/** 정리 배치 즉시 실행. 되돌릴 수 없는 작업이라 확인 체크박스를 요구한다. */
export async function runCleanup(
  _prev: ActionResult | null,
  formData: FormData,
): Promise<ActionResult> {
  const profile = await requireOperator();
  // 그냥 인덱싱하면 preset=constructor·__proto__ 같은 Object.prototype 속성이
  // truthy로 잡혀 허용 목록 검사를 지나간다 (keywords/actions.ts와 같은 방식)
  const presetKey = String(formData.get("preset") ?? "");
  const preset = Object.hasOwn(CLEANUP_PRESETS, presetKey)
    ? CLEANUP_PRESETS[presetKey]
    : undefined;
  if (!preset) return { ok: false, message: "허용되지 않은 정리 작업입니다." };
  if (formData.get("confirm") !== "on") {
    return {
      ok: false,
      message: "되돌릴 수 없는 작업입니다. 확인 체크박스를 켠 뒤 다시 누르세요.",
    };
  }
  if (await isWorkflowBusy("cleanup.yml")) {
    return {
      ok: false,
      message:
        "정리 배치가 아직 실행 중입니다. 끝난 뒤 다시 누르세요 — 지금 누르면 뒤 실행이 " +
        "앞 실행을 기다리기만 합니다. (운영 > 사용량 표에서 cleanup 행의 RUNNING 여부를 볼 수 있습니다)",
    };
  }

  try {
    await dispatchWorkflow("cleanup.yml", preset.tasks ? { tasks: preset.tasks } : {});
  } catch (e) {
    return {
      ok: false,
      message: `실행 요청에 실패했습니다: ${e instanceof Error ? e.message : String(e)}`,
    };
  }

  const supabase = createServiceRoleClient();
  await supabase.from("operation_events").insert({
    event_type: "DATA_CLEANUP_DISPATCH",
    target_table: "workflow_usage",
    actor_id: profile.id,
    reason: `정리 배치 실행 — ${preset.label}${preset.tasks ? ` (tasks=${preset.tasks})` : ""}`,
  });
  revalidatePath("/admin/data");
  return {
    ok: true,
    message:
      `실행을 요청했습니다 (${preset.label}). 30초~2분 내 시작하고 몇 분 안에 끝납니다. ` +
      `결과 건수는 아래 '최근 정리 실행'과 운영 > 사용량 표의 cleanup 행 비고에 남습니다. ` +
      `비운 공간이 DB 용량 수치에 반영되려면 VACUUM FULL(아래 안내)이 필요합니다.`,
  };
}
