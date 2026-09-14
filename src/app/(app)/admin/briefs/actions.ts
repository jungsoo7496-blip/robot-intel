"use server";

import { revalidatePath } from "next/cache";

import { requireOperator } from "@/lib/auth";
import { createServiceRoleClient } from "@/lib/supabase/server";

import { dispatchWorkflow, isWorkflowBusy } from "@/lib/github-workflow";

/** 폼 결과 — regenerate-form.tsx의 useActionState가 받는 { ok, message } 패턴. */
export type RegenerateState = { ok: boolean; message: string } | null;

const BRIEF_WORKFLOW = "generate-brief.yml";

/**
 * 하루(24시간) 재생성 상한 — 무료 AI 한도 보호.
 * 브리프 1회는 최대 6콜(섹션 3개 × 기본·예비 모델, generate_brief.MAX_BRIEF_CALLS).
 * 5회 × 6콜 = 30콜에 정기 배치 1회를 더해도 기사 분석 몫(하루 450콜)을 건드리지
 * 않는다. 모델명은 app_settings(brief_model)에서 바뀌므로 여기에 적지 않는다.
 */
const BRIEF_REGENERATE_DAILY_LIMIT = 5;

function kstToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(
    new Date(),
  );
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/**
 * YYYY-MM-DD → 그 날짜가 속한 주(월~일). 형식 오류·없는 날짜면 null.
 * generate_brief.py의 resolve_week_start와 같은 규칙 — 월요일이 아니면
 * 그 주 월요일로 보정한다. 날짜만 다루므로 UTC로 계산해 서버 시간대와 무관하다.
 */
function resolveWeek(
  raw: string,
): { start: string; end: string; adjusted: boolean } | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) return null;
  const d = new Date(`${raw}T00:00:00Z`);
  if (Number.isNaN(d.getTime()) || isoDate(d) !== raw) return null;
  const weekday = (d.getUTCDay() + 6) % 7; // 월=0 … 일=6
  const start = new Date(d);
  start.setUTCDate(d.getUTCDate() - weekday);
  const end = new Date(start);
  end.setUTCDate(start.getUTCDate() + 6);
  return { start: isoDate(start), end: isoDate(end), adjusted: weekday !== 0 };
}

/** 최근 24시간 동안 화면에서 요청한 재생성 횟수 (operation_events 기준). */
async function countRecentRegenerates(
  supabase: ReturnType<typeof createServiceRoleClient>,
): Promise<number> {
  const since = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
  const { count } = await supabase
    .from("operation_events")
    .select("id", { count: "exact", head: true })
    .eq("event_type", "BRIEF_REGENERATE")
    .gte("created_at", since);
  return count ?? 0;
}

/** 관리 화면 상단 표시용: 24시간 사용 횟수·상한·지금 실행 중인지. */
export async function getRegenerateStatus(): Promise<{
  used: number;
  limit: number;
  busy: boolean;
}> {
  await requireOperator();
  const supabase = createServiceRoleClient();
  const [used, busy] = await Promise.all([
    countRecentRegenerates(supabase),
    isWorkflowBusy(BRIEF_WORKFLOW),
  ]);
  return { used, limit: BRIEF_REGENERATE_DAILY_LIMIT, busy };
}

/**
 * 브리프 재생성(주차 지정, 사용자 요구 5): generate-brief 워크플로를
 * week_start 지정으로 즉시 트리거한다. 배치는 기발행이어도 새 버전을 만들고,
 * 성공하면 새 버전이 현재본이 된다 (generate_brief.save_brief).
 */
export async function regenerateBrief(
  _prev: RegenerateState,
  formData: FormData,
): Promise<RegenerateState> {
  const profile = await requireOperator();

  // 직접 입력한 날짜가 있으면 그것을 우선, 없으면 드롭다운(또는 행의 숨은 값)
  const manual = String(formData.get("week_start_manual") ?? "").trim();
  const selected = String(formData.get("week_start") ?? "").trim();
  const raw = manual || selected;
  if (!raw) {
    return { ok: false, message: "주차를 고르거나 날짜를 입력하세요." };
  }

  const week = resolveWeek(raw);
  if (!week) {
    return {
      ok: false,
      message: `날짜 형식이 올바르지 않습니다: ${raw} (예: 2026-08-31)`,
    };
  }
  const today = kstToday();
  if (week.end >= today) {
    return {
      ok: false,
      message:
        `${week.start} ~ ${week.end} 주차는 아직 끝나지 않았거나 미래 주차입니다 ` +
        `(오늘 ${today}). 일요일이 지난 뒤(월요일부터) 만들 수 있습니다.`,
    };
  }

  const supabase = createServiceRoleClient();
  const used = await countRecentRegenerates(supabase);
  if (used >= BRIEF_REGENERATE_DAILY_LIMIT) {
    return {
      ok: false,
      message:
        `24시간에 ${BRIEF_REGENERATE_DAILY_LIMIT}회까지입니다 (지금까지 ${used}회). ` +
        `정기 생성은 이 제한과 무관하게 계속 돕니다.`,
    };
  }
  if (await isWorkflowBusy(BRIEF_WORKFLOW)) {
    return {
      ok: false,
      message: "지금 생성 중입니다. 2~3분 뒤 새로고침해 결과를 확인하세요.",
    };
  }

  try {
    // workflow_dispatch REST API는 input을 전부 문자열로 받는다 —
    // boolean input(force)도 "true" 문자열로 보내면 워크플로에서 true가 된다 (gh CLI와 동일).
    await dispatchWorkflow(BRIEF_WORKFLOW, {
      force: "true",
      week_start: week.start,
    });
  } catch (e) {
    return {
      ok: false,
      message: `실행 요청에 실패했습니다: ${e instanceof Error ? e.message : String(e)}`,
    };
  }

  // 이미 기간 행이 있으면(재생성) target_id로 연결, 없으면(첫 생성) null
  const { data: period } = await supabase
    .from("brief_periods")
    .select("id")
    .eq("period_start", week.start)
    .eq("period_end", week.end)
    .maybeSingle();
  const adjustedNote = week.adjusted
    ? ` (입력 ${raw} → 그 주 월요일 ${week.start}로 보정)`
    : "";
  await supabase.from("operation_events").insert({
    event_type: "BRIEF_REGENERATE",
    target_table: "brief_periods",
    target_id: period?.id ?? null,
    actor_id: profile.id,
    reason:
      `${week.start} ~ ${week.end} 주차 브리프 ${period ? "재생성" : "생성"} 요청 ` +
      `(workflow_dispatch)${adjustedNote}`,
  });
  revalidatePath("/admin/briefs");
  return {
    ok: true,
    message:
      `${week.start} ~ ${week.end} 주차 생성을 요청했습니다${adjustedNote}. ` +
      `1~3분 뒤 새로고침하면 표에 나타납니다. ` +
      `(24시간 기준 ${used + 1}/${BRIEF_REGENERATE_DAILY_LIMIT}회)`,
  };
}
