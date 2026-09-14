"use server";

import { revalidatePath } from "next/cache";

import { requireOperator } from "@/lib/auth";
import { createServiceRoleClient } from "@/lib/supabase/server";

import { dispatchWorkflow, isWorkflowBusy } from "@/lib/github-workflow";

/**
 * 보고서 수집원·자료 종류 관리 (사용자 요구 2번, 보고서 스펙 §31).
 *
 * - 수집원(report_sources)·자료 종류(report_source_channels) 추가·편집·정리·활성 토글
 * - 자료 종류 '지금 실행 대상으로' (last_run_at 초기화 + 조합 상태 삭제) ·
 *   보고서 수집 워크플로 즉시 실행
 * - 비밀값(API 키 등)은 절대 받지도 저장하지도 않는다 — credential_key_name은
 *   GitHub Actions secret의 '이름'만 담는다.
 *
 * 검색어는 여기서 다루지 않는다: 공용 목록(search_keywords)을 [검색 키워드]
 * 화면에서 관리하고, 배치가 실행 시점에 (자료 종류 × 검색어)로 곱한다. 그래서
 * report_source_channels.query 는 항상 NULL 로 저장한다.
 */

export type ActionState = { ok: boolean; message: string } | null;

const PAGE_PATH = "/admin/report-sources";
const COLLECT_REPORTS_WORKFLOW = "collect-reports.yml";

// ------------------------------------------------------------
// 선택지·어댑터 안내 — 파이썬 쪽과 수동 동기화한다
// ------------------------------------------------------------

/** report_sources.status CHECK (20260808000020_report_collection.sql). */
const SOURCE_STATUSES = [
  { value: "CANDIDATE", label: "후보 — 아직 검토 중" },
  { value: "NEEDS_ADAPTER", label: "adapter 필요 — 수집 코드 없음" },
  { value: "READY", label: "준비됨 — 아직 수집 안 함" },
  { value: "ACTIVE", label: "정상 — 배치가 수집" },
  { value: "PAUSED", label: "일시중지" },
  { value: "BROKEN", label: "고장" },
  { value: "RETIRED", label: "종료" },
] as const;

/** report_source_channels.collection_method CHECK. */
const COLLECTION_METHODS = ["API", "HTML", "RSS", "CSV", "OTHER"] as const;

export type AdapterInfo = {
  label: string;
  /** config_json textarea placeholder (어댑터가 실제로 읽는 키). */
  configExample: string;
  /** config_json 키 설명 — 어댑터 파일에서 확인한 값. */
  configHelp: string;
  /** 채널 credential_key_name 기본값 (없으면 null). */
  credentialKeyName: string | null;
  /** 채널 설정과 별개로 워크플로 env로만 주입되는 추가 키 이름. */
  extraEnvNames: string[];
  note?: string;
};

/**
 * 어댑터 레지스트리 — scripts/kiro_batch/reports/registry.py 의 키와
 * 각 어댑터 파일이 channel.config 에서 읽는 키를 옮겨 적었다 (수동 동기화).
 */
const ADAPTER_INFO: Record<string, AdapterInfo> = {
  point: {
    label: "point — 정책정보포털(POINT, 국립중앙도서관)",
    configExample: '{"category": "chamgo"}',
    configHelp:
      "category: jonghab(종합) · chamgo(정책자료, 기본) · digital(디지털 정책자료)",
    credentialKeyName: null,
    extraEnvNames: [],
    note:
      "인증키 불필요. 데이터센터 IP가 차단돼 GitHub Actions에서는 건너뛰고(REPORT_SKIP_SOURCES) 사무실 PC 예약 실행이 수집합니다.",
  },
  prism: {
    label: "prism — 온-나라 정책연구(PRISM, data.go.kr)",
    configExample: '{"years_back": 1}',
    configHelp: "years_back: 올해부터 몇 년 전까지 조회할지 (기본 1)",
    credentialKeyName: "PRISM_API_KEY",
    extraEnvNames: [],
  },
  nanet: {
    label: "nanet — 국회도서관 자료검색",
    configExample: '{"dbname": "웹자료", "year_from": 2021}',
    configHelp:
      "dbname: 웹자료(기본) · 세미나자료 등 / year_from · year_to: 발행년도 범위 (기본 5년 전 ~ 올해)",
    credentialKeyName: "DATA_GO_KR_API_KEY",
    extraEnvNames: ["NANET_DETAIL_API_KEY"],
    note: "상세정보(초록·키워드)는 별도 키 NANET_DETAIL_API_KEY를 워크플로 env에서 읽습니다.",
  },
  nkis: {
    label: "nkis — 국가정책연구포털(NKIS)",
    configExample: '{"year_from": 2021}',
    configHelp: "year_from: 발행연도 시작 (비우면 전체 연도)",
    credentialKeyName: "NKIS_API_KEY",
    extraEnvNames: [],
  },
  alio: {
    label: "alio — 알리오(ALIO) 공공기관 연구보고서",
    configExample: '{"search_type": "title"}',
    configHelp: "search_type: 검색 대상 필드 (기본 title)",
    credentialKeyName: null,
    extraEnvNames: [],
    note: "인증키 불필요. Actions에서는 서울 중계(REPORT_RELAY_URL) 경유.",
  },
  scienceon: {
    label: "scienceon — ScienceON(KISTI)",
    configExample: '{"year_from": 2021}',
    configHelp: "year_from: 조회 시작 연도 (기본 2021) — 올해부터 거꾸로 연도별 조회",
    credentialKeyName: "SCIENCEON_AUTH_KEY",
    extraEnvNames: ["SCIENCEON_CLIENT_ID", "SCIENCEON_MAC_ADDRESS"],
    note: "토큰 발급에 SCIENCEON_CLIENT_ID · SCIENCEON_MAC_ADDRESS env도 필요합니다 (워크플로에서 주입).",
  },
};
const ADAPTER_KEYS = Object.keys(ADAPTER_INFO);

/**
 * .github/workflows/collect-reports.yml 의 env 목록 — 워크플로와 수동 동기화.
 * (파일을 읽지 않는다: 화면은 Vercel에서 돌고 워크플로 파일은 저장소에 있다.)
 * 채널 credential_key_name이 여기 없으면 배치가 '자격증명 없음'으로 실패하므로
 * 저장 시 경고한다.
 */
const COLLECT_REPORTS_WORKFLOW_ENV_NAMES = [
  "SUPABASE_DB_URL",
  "GEMINI_API_KEY",
  "PRISM_API_KEY",
  "DATA_GO_KR_API_KEY",
  "NANET_DETAIL_API_KEY",
  "NKIS_API_KEY",
  "SCIENCEON_AUTH_KEY",
  "SCIENCEON_CLIENT_ID",
  "SCIENCEON_MAC_ADDRESS",
  "REPORT_RELAY_URL",
  "REPORT_RELAY_TOKEN",
  "REPORT_AI_MAX_CALLS_PER_RUN",
  "REPORT_AI_DAILY_CALL_LIMIT",
  "REPORT_SKIP_SOURCES",
];

/**
 * 채널 '필요한 키 이름'으로 쓸 수 없는 env — DB 접속 문자열·AI 키·중계 토큰과
 * 실행 설정값. 배치는 이 이름의 env 값을 그대로 어댑터에 넘겨 외부 API의 GET
 * 쿼리(serviceKey=)로 보내므로, 골라 넣기만 해도 인프라 비밀이 제3자에게
 * 전송되고 실패 문구로 DB·화면에 남는다. (scripts/kiro_batch/collect_reports.py
 * 의 _NON_CREDENTIAL_ENV_NAMES 와 같은 목록 — 함께 고칠 것)
 */
const NON_CREDENTIAL_ENV_NAMES = new Set([
  "SUPABASE_DB_URL",
  "GEMINI_API_KEY",
  "REPORT_RELAY_URL",
  "REPORT_RELAY_TOKEN",
  "REPORT_AI_MAX_CALLS_PER_RUN",
  "REPORT_AI_DAILY_CALL_LIMIT",
  "REPORT_SKIP_SOURCES",
]);

/** 채널 키 이름 후보 — 어댑터 자격증명용 env만 (추천 목록·경고 판단에 쓴다). */
const CREDENTIAL_ENV_NAMES = COLLECT_REPORTS_WORKFLOW_ENV_NAMES.filter(
  (n) => !NON_CREDENTIAL_ENV_NAMES.has(n),
);

export type ReportSourceFormOptions = {
  statuses: { value: string; label: string }[];
  adapterKeys: string[];
  adapterInfo: Record<string, AdapterInfo>;
  collectionMethods: string[];
  workflowEnvNames: string[];
  blockedEnvNames: string[];
};

/** 폼·표가 쓰는 선택지 (서버 컴포넌트에서 호출해 props로 내려준다). */
export async function getReportSourceFormOptions(): Promise<ReportSourceFormOptions> {
  await requireOperator();
  return {
    statuses: SOURCE_STATUSES.map((s) => ({ value: s.value, label: s.label })),
    adapterKeys: ADAPTER_KEYS,
    adapterInfo: ADAPTER_INFO,
    collectionMethods: [...COLLECTION_METHODS],
    workflowEnvNames: CREDENTIAL_ENV_NAMES,
    blockedEnvNames: [...NON_CREDENTIAL_ENV_NAMES],
  };
}

// ------------------------------------------------------------
// 행 타입 (page → 폼 props)
// ------------------------------------------------------------

export type ReportSourceRow = {
  id: string;
  source_key: string;
  name: string;
  base_url: string | null;
  source_kind: string | null;
  status: string;
  priority: number;
  owner_org: string | null;
  description: string | null;
  notes: string | null;
  adapter_key: string | null;
};

export type ReportChannelRow = {
  id: string;
  source_id: string;
  channel_key: string;
  name: string;
  adapter_key: string | null;
  collection_method: string;
  enabled: boolean;
  /** 항상 NULL(검색어는 공용 목록). 은퇴 자료 종류는 통합 전 검색어가 남아 있다. */
  query: string | null;
  /** true = 검색어 목록으로 찾는다 · false = 검색어 없이 전체를 본다(prism). */
  uses_keywords: boolean;
  /** 값이 있으면 '검색 키워드로 통합된 옛 채널' — 실행하지 않고 기록만 남긴다. */
  retired_at: string | null;
  config_json: Record<string, unknown> | null;
  fetch_interval_hours: number;
  max_pages_per_run: number;
  max_items_per_run: number;
  request_interval_ms: number;
  credential_key_name: string | null;
  supports_abstract: boolean;
  supports_file_metadata: boolean;
  supports_direct_download: boolean;
  supports_viewer: boolean;
  last_run_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
};

// ------------------------------------------------------------
// 공통 헬퍼
// ------------------------------------------------------------

type Supabase = ReturnType<typeof createServiceRoleClient>;

/**
 * 운영 조치 이력 (tasks §14.2).
 * operation_events.event_type 에는 CHECK 목록이 있어(core_tables.sql) 아직
 * REPORT_SOURCE_*·REPORT_CHANNEL_* 가 허용되지 않을 수 있다 — 거부되면(23514)
 * 'OTHER'로 기록하고 원래 종류를 detail·reason에 남긴다. CHECK가 확장되면
 * 자동으로 본래 종류로 기록된다.
 */
async function logEvent(
  supabase: Supabase,
  eventType: string,
  targetTable: string,
  targetId: string | null,
  actorId: string,
  reason?: string,
  detail?: Record<string, unknown>,
) {
  const row = {
    event_type: eventType,
    target_table: targetTable,
    target_id: targetId,
    actor_id: actorId,
    reason: reason ?? null,
    detail: detail ?? null,
  };
  const { error } = await supabase.from("operation_events").insert(row);
  if (error?.code === "23514") {
    await supabase.from("operation_events").insert({
      ...row,
      event_type: "OTHER",
      reason: `${eventType}${reason ? ` — ${reason}` : ""}`,
      detail: { ...(detail ?? {}), intended_event_type: eventType },
    });
  }
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function uuidField(formData: FormData, name: string): string | null {
  const v = String(formData.get(name) ?? "").trim();
  return UUID_RE.test(v) ? v : null;
}

/** 빈 문자열은 null, 아니면 trim 후 길이 제한. */
function textField(formData: FormData, name: string, max = 500): string | null {
  const v = String(formData.get(name) ?? "").trim();
  return v ? v.slice(0, max) : null;
}

function boolField(formData: FormData, name: string): boolean {
  const v = formData.get(name);
  return v === "on" || v === "true" || v === "1";
}

/** 정수 필드 — 비우면 fallback, 범위 밖이면 오류 문구. */
function intField(
  formData: FormData,
  name: string,
  label: string,
  min: number,
  max: number,
  fallback: number,
): { value: number } | { error: string } {
  const raw = String(formData.get(name) ?? "").trim();
  if (!raw) return { value: fallback };
  const n = Number(raw);
  if (!Number.isInteger(n) || n < min || n > max) {
    return { error: `${label}은(는) ${min}~${max} 사이의 정수여야 합니다.` };
  }
  return { value: n };
}

function fail(message: string): ActionState {
  return { ok: false, message };
}

/**
 * 검색 키워드 통합 마이그레이션(20260908000026_search_keywords.sql)이 아직
 * 적용되지 않아 생기는 오류인지. 이 마이그레이션이 report_source_channels의
 * uses_keywords·retired_at 컬럼과 report_channel_keyword_runs 표를 만든다.
 *
 * PostgREST는 스키마 캐시에 없는 표를 PGRST205·없는 컬럼을 PGRST204로 돌려주고,
 * SQL 오류가 그대로 새면 42P01(표 없음)·42703(컬럼 없음)이 온다.
 * ([검색 키워드] 화면 keyword-fields.ts의 isMissingTableError 와 같은 판정 —
 *  판정 기준이 바뀌면 두 곳을 함께 고칠 것)
 */
function isMissingSchemaError(
  error: { code?: string; message: string } | null | undefined,
): boolean {
  if (!error) return false;
  return (
    error.code === "42P01" ||
    error.code === "PGRST205" ||
    error.code === "42703" ||
    error.code === "PGRST204" ||
    /schema cache/i.test(error.message)
  );
}

/**
 * 마이그레이션 26 적용 전 안내. 컬럼을 빼고 저장해 주지는 않는다 —
 * uses_keywords 기본값이 true라서, '검색어 없이 전체를 봅니다'로 저장한 자료 종류가
 * 마이그레이션 뒤 조용히 '검색어 사용'으로 되살아나기 때문이다.
 */
const MIGRATION_PENDING_MESSAGE =
  "데이터베이스에 새 항목(자료 종류의 '검색어 사용' 여부 등)이 아직 만들어지지 않아 저장하지 않았습니다. " +
  "새 마이그레이션이 적용된 뒤에 다시 저장해 주세요.";

function dbErrorMessage(error: { code?: string; message: string }, dupMessage: string) {
  if (isMissingSchemaError(error)) return MIGRATION_PENDING_MESSAGE;
  if (error.code === "23505") return dupMessage;
  if (error.code === "23503") return "연결된 수집원을 찾을 수 없습니다. 화면을 새로고침해 주세요.";
  if (error.code === "23514") return "허용되지 않은 값이 있습니다. 선택지를 확인해 주세요.";
  return `저장에 실패했습니다: ${error.message}`;
}

// ------------------------------------------------------------
// 수집원 (report_sources)
// ------------------------------------------------------------

const SOURCE_KEY_RE = /^[a-z][a-z0-9_-]{1,39}$/;
const SOURCE_KIND_RE = /^[A-Z0-9_ -]{1,40}$/;

type SourceValues = {
  name: string;
  base_url: string | null;
  source_kind: string | null;
  status: string;
  priority: number;
  owner_org: string | null;
  description: string | null;
  notes: string | null;
  adapter_key: string | null;
};

function parseSourceFields(formData: FormData): { values: SourceValues } | { error: string } {
  const name = textField(formData, "name", 120);
  if (!name) return { error: "이름을 입력해 주세요." };

  const base_url = textField(formData, "base_url", 500);
  if (base_url) {
    if (!/^https?:\/\//i.test(base_url)) return { error: "기본 URL은 http(s)://로 시작해야 합니다." };
    try {
      new URL(base_url);
    } catch {
      return { error: "기본 URL 형식이 올바르지 않습니다." };
    }
  }

  const kindRaw = textField(formData, "source_kind", 40);
  const source_kind = kindRaw ? kindRaw.toUpperCase() : null;
  if (source_kind && !SOURCE_KIND_RE.test(source_kind)) {
    return { error: "종류는 영문·숫자·밑줄로 적어 주세요 (예: PORTAL, LIBRARY, INSTITUTE)." };
  }

  const status = String(formData.get("status") ?? "");
  if (!SOURCE_STATUSES.some((s) => s.value === status)) {
    return { error: "상태 선택이 올바르지 않습니다." };
  }

  const priority = intField(formData, "priority", "우선순위", 0, 10000, 100);
  if ("error" in priority) return priority;

  const adapterRaw = String(formData.get("adapter_key") ?? "").trim();
  if (adapterRaw && !ADAPTER_KEYS.includes(adapterRaw)) {
    return { error: "어댑터 선택이 올바르지 않습니다." };
  }

  return {
    values: {
      name,
      base_url,
      source_kind,
      status,
      priority: priority.value,
      owner_org: textField(formData, "owner_org", 120),
      description: textField(formData, "description", 1000),
      notes: textField(formData, "notes", 2000),
      adapter_key: adapterRaw || null,
    },
  };
}

/** 수집원 추가. source_key는 만든 뒤 바꿀 수 없다 (배치·통계가 키로 참조). */
export async function createReportSource(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const profile = await requireOperator();

  const source_key = String(formData.get("source_key") ?? "").trim().toLowerCase();
  if (!SOURCE_KEY_RE.test(source_key)) {
    return fail("source_key는 영문 소문자로 시작하는 2~40자(소문자·숫자·-·_)여야 합니다. 예: kdi, kiet-report");
  }
  const parsed = parseSourceFields(formData);
  if ("error" in parsed) return fail(parsed.error);

  const supabase = createServiceRoleClient();
  const { data, error } = await supabase
    .from("report_sources")
    .insert({ source_key, ...parsed.values })
    .select("id")
    .single();
  if (error || !data) {
    return fail(
      dbErrorMessage(error ?? { message: "알 수 없는 오류" }, `source_key '${source_key}'는 이미 있습니다. 다른 키를 쓰세요.`),
    );
  }

  await logEvent(
    supabase, "REPORT_SOURCE_CREATE", "report_sources", data.id, profile.id,
    `${source_key} · ${parsed.values.name}`,
    { source_key, status: parsed.values.status, adapter_key: parsed.values.adapter_key },
  );
  revalidatePath(PAGE_PATH);

  const hint = parsed.values.adapter_key
    ? "아래에서 자료 종류를 하나 추가하고 상태를 '정상'으로 두면 다음 수집부터 검색어 목록으로 찾습니다."
    : "어댑터가 없으면 배치가 수집하지 못합니다 — 어댑터 구현 후 편집에서 연결하세요.";
  return { ok: true, message: `수집원 '${parsed.values.name}'을(를) 추가했습니다. ${hint}` };
}

/** 수집원 편집 (source_key 제외 전 항목). */
export async function updateReportSource(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const profile = await requireOperator();
  const id = uuidField(formData, "id");
  if (!id) return fail("대상 수집원을 찾을 수 없습니다.");
  const parsed = parseSourceFields(formData);
  if ("error" in parsed) return fail(parsed.error);

  const supabase = createServiceRoleClient();
  const { data: before } = await supabase
    .from("report_sources")
    .select("source_key, status, adapter_key")
    .eq("id", id)
    .maybeSingle();
  if (!before) return fail("대상 수집원을 찾을 수 없습니다.");

  const { error } = await supabase
    .from("report_sources")
    .update({ ...parsed.values, updated_at: new Date().toISOString() })
    .eq("id", id);
  if (error) return fail(dbErrorMessage(error, "저장에 실패했습니다."));

  await logEvent(
    supabase, "REPORT_SOURCE_UPDATE", "report_sources", id, profile.id,
    `${before.source_key} · ${parsed.values.name}`,
    {
      status: { from: before.status, to: parsed.values.status },
      adapter_key: { from: before.adapter_key, to: parsed.values.adapter_key },
    },
  );
  revalidatePath(PAGE_PATH);
  return { ok: true, message: "저장했습니다." };
}

/**
 * 수집원 삭제 — 수집된 자료(report_occurrences)가 없을 때만 하드 삭제한다
 * (채널·실행 통계는 ON DELETE CASCADE). 자료가 있으면 삭제 대신 '종료'를 안내.
 */
export async function deleteReportSource(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const profile = await requireOperator();
  const id = uuidField(formData, "id");
  if (!id) return fail("대상 수집원을 찾을 수 없습니다.");

  const supabase = createServiceRoleClient();
  const [{ data: source }, { count: occurrences }, { count: channels }] = await Promise.all([
    supabase.from("report_sources").select("source_key, name, status").eq("id", id).maybeSingle(),
    supabase.from("report_occurrences").select("id", { count: "exact", head: true }).eq("source_id", id),
    supabase.from("report_source_channels").select("id", { count: "exact", head: true }).eq("source_id", id),
  ]);
  if (!source) return fail("대상 수집원을 찾을 수 없습니다.");

  if ((occurrences ?? 0) > 0) {
    return fail(
      `이 수집원으로 모은 자료가 ${occurrences}건 있어 삭제하지 않습니다 (자료·분석 이력이 같이 사라집니다). ` +
        `대신 상태를 '종료(RETIRED)'로 바꾸면 배치가 더 이상 수집하지 않고 기존 자료는 남습니다.`,
    );
  }

  const { error } = await supabase.from("report_sources").delete().eq("id", id);
  if (error) return fail(`삭제에 실패했습니다: ${error.message}`);

  await logEvent(
    supabase, "REPORT_SOURCE_DELETE", "report_sources", id, profile.id,
    `${source.source_key} · ${source.name} (채널 ${channels ?? 0}개 함께 삭제)`,
    { source_key: source.source_key, name: source.name, channel_count: channels ?? 0 },
  );
  revalidatePath(PAGE_PATH);
  return { ok: true, message: `수집원 '${source.name}'과 자료 종류 ${channels ?? 0}개를 삭제했습니다.` };
}

/** 수집원 종료(RETIRED) 전환 — 자료가 있어 삭제 못 하는 수집원의 대안. */
export async function retireReportSource(formData: FormData) {
  const profile = await requireOperator();
  const id = uuidField(formData, "id");
  if (!id) return;

  const supabase = createServiceRoleClient();
  const { data: source } = await supabase
    .from("report_sources")
    .select("source_key, status")
    .eq("id", id)
    .maybeSingle();
  if (!source || source.status === "RETIRED") return;

  const { error } = await supabase
    .from("report_sources")
    .update({ status: "RETIRED", updated_at: new Date().toISOString() })
    .eq("id", id);
  // 실패해도 예외가 아니라 error로 돌아온다 — 그냥 두면 바뀐 게 없는데도
  // 이력에는 '종료함'이 남는다. 화면은 다음 로드에서 실제 상태를 보여준다.
  if (error) {
    console.error("[retireReportSource] update 실패", {
      id,
      code: error.code,
      message: error.message,
    });
    return;
  }
  await logEvent(
    supabase, "REPORT_SOURCE_RETIRE", "report_sources", id, profile.id,
    `${source.source_key}: ${source.status} → RETIRED`,
  );
  revalidatePath(PAGE_PATH);
}

// ------------------------------------------------------------
// 채널 (report_source_channels)
// ------------------------------------------------------------

const CHANNEL_KEY_RE = /^[a-z0-9][a-z0-9_-]{0,59}$/;
const ENV_NAME_RE = /^[A-Z][A-Z0-9_]{1,63}$/;

type ChannelValues = {
  channel_key: string;
  name: string;
  adapter_key: string | null;
  collection_method: string;
  enabled: boolean;
  /** 항상 null — 검색어는 공용 목록(search_keywords)이 갖는다. */
  query: null;
  uses_keywords: boolean;
  config_json: Record<string, unknown> | null;
  fetch_interval_hours: number;
  max_pages_per_run: number;
  max_items_per_run: number;
  request_interval_ms: number;
  credential_key_name: string | null;
  supports_abstract: boolean;
  supports_file_metadata: boolean;
  supports_direct_download: boolean;
  supports_viewer: boolean;
};

function parseChannelFields(formData: FormData): { values: ChannelValues } | { error: string } {
  const channel_key = String(formData.get("channel_key") ?? "").trim().toLowerCase();
  if (!CHANNEL_KEY_RE.test(channel_key)) {
    return { error: "자료 종류 키는 영문 소문자·숫자·-·_ 로 1~60자여야 합니다. 예: web" };
  }
  const name = textField(formData, "name", 120);
  if (!name) return { error: "자료 종류 이름을 입력해 주세요." };

  const adapterRaw = String(formData.get("adapter_key") ?? "").trim();
  if (adapterRaw && !ADAPTER_KEYS.includes(adapterRaw)) {
    return { error: "어댑터 선택이 올바르지 않습니다." };
  }
  const collection_method = String(formData.get("collection_method") ?? "API");
  if (!(COLLECTION_METHODS as readonly string[]).includes(collection_method)) {
    return { error: "수집 방식 선택이 올바르지 않습니다." };
  }

  // config_json: 비우면 null, 있으면 JSON '객체'여야 한다
  let config_json: Record<string, unknown> | null = null;
  const configRaw = String(formData.get("config_json") ?? "").trim();
  if (configRaw) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(configRaw);
    } catch {
      return { error: '추가 설정(JSON) 형식이 잘못됐습니다. 예: {"year_from": 2021} — 따옴표는 큰따옴표(")를 쓰세요.' };
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { error: '추가 설정(JSON)은 { } 로 감싼 객체여야 합니다. 예: {"year_from": 2021}' };
    }
    config_json = parsed as Record<string, unknown>;
  }

  const fetchInterval = intField(formData, "fetch_interval_hours", "수집 주기(시간)", 1, 720, 24);
  if ("error" in fetchInterval) return fetchInterval;
  const maxPages = intField(formData, "max_pages_per_run", "실행당 최대 페이지", 1, 100, 3);
  if ("error" in maxPages) return maxPages;
  const maxItems = intField(formData, "max_items_per_run", "실행당 최대 건수", 1, 2000, 200);
  if ("error" in maxItems) return maxItems;
  const interval = intField(formData, "request_interval_ms", "요청 간격(ms)", 0, 60000, 1500);
  if ("error" in interval) return interval;

  // 키 '이름'만 허용 — 소문자·기호가 섞인 값(실제 키를 붙여넣은 경우)은 거부한다
  const credential_key_name = textField(formData, "credential_key_name", 64);
  if (credential_key_name && !ENV_NAME_RE.test(credential_key_name)) {
    return {
      error:
        "필요한 키 이름은 대문자·숫자·밑줄로 된 env 변수 이름이어야 합니다 (예: NKIS_API_KEY). " +
        "실제 키 값을 넣지 마세요 — 값은 GitHub Secrets에만 둡니다.",
    };
  }
  // 배치는 이 이름의 env 값을 그대로 외부 API에 보낸다 — 인프라 비밀은 막는다
  if (credential_key_name && NON_CREDENTIAL_ENV_NAMES.has(credential_key_name)) {
    return {
      error:
        `'${credential_key_name}'은(는) 데이터베이스·AI·중계용 비밀이라 채널 키 이름으로 쓸 수 없습니다. ` +
        "그대로 두면 배치가 이 값을 외부 사이트에 그대로 보냅니다. " +
        "어댑터용 키 이름(예: NKIS_API_KEY)을 넣으세요.",
    };
  }

  return {
    values: {
      channel_key,
      name,
      adapter_key: adapterRaw || null,
      collection_method,
      enabled: boolField(formData, "enabled"),
      // 검색어는 [검색 키워드] 화면의 공용 목록이 갖는다 — 자료 종류에는 저장하지 않는다
      query: null,
      uses_keywords: boolField(formData, "uses_keywords"),
      config_json,
      fetch_interval_hours: fetchInterval.value,
      max_pages_per_run: maxPages.value,
      max_items_per_run: maxItems.value,
      request_interval_ms: interval.value,
      credential_key_name,
      supports_abstract: boolField(formData, "supports_abstract"),
      supports_file_metadata: boolField(formData, "supports_file_metadata"),
      supports_direct_download: boolField(formData, "supports_direct_download"),
      supports_viewer: boolField(formData, "supports_viewer"),
    },
  };
}

/**
 * 보고서 수집에 쓰이는 공용 검색어 개수 — search_keywords 의 scope ALL·REPORTS.
 * 표가 아직 없거나(마이그레이션 전) 조회가 실패하면 null 을 돌려주고 경고를
 * 생략한다 (저장 자체를 막지는 않는다).
 */
async function countReportKeywords(supabase: Supabase): Promise<number | null> {
  const { count, error } = await supabase
    .from("search_keywords")
    .select("term", { count: "exact", head: true })
    .in("scope", ["ALL", "REPORTS"]);
  if (error) return null;
  return count ?? 0;
}

/** 저장 후 운영자에게 알려줄 주의 사항 (저장은 됐지만 배치가 못 돌 수 있는 경우). */
function channelWarnings(
  values: ChannelValues,
  source: { status: string; adapter_key: string | null },
  keywordCount: number | null,
): string[] {
  const out: string[] = [];
  if (values.uses_keywords && keywordCount === 0) {
    out.push(
      "[검색 키워드] 화면에 보고서용 검색어가 하나도 없어 이 자료 종류는 아무것도 찾지 못합니다 — 검색어를 먼저 추가하세요.",
    );
  }
  const effectiveAdapter = values.adapter_key ?? source.adapter_key;
  if (!effectiveAdapter) {
    out.push("어댑터가 없어(자료 종류·수집원 모두 비어 있음) 배치가 이 자료 종류를 실행하지 못합니다.");
  }
  if (values.credential_key_name && !CREDENTIAL_ENV_NAMES.includes(values.credential_key_name)) {
    out.push(
      `키 이름 '${values.credential_key_name}'은(는) collect-reports.yml의 env 목록에 없습니다. ` +
        `GitHub Secrets 등록 + 워크플로 env 추가 전에는 배치가 '자격증명 없음'으로 이 자료 종류를 실패 처리합니다.`,
    );
  }
  if (effectiveAdapter && ADAPTER_INFO[effectiveAdapter]?.credentialKeyName && !values.credential_key_name) {
    out.push(
      `${effectiveAdapter} 어댑터는 보통 ${ADAPTER_INFO[effectiveAdapter].credentialKeyName} 키가 필요합니다 — 키 이름이 비어 있으면 인증 없이 호출해 실패할 수 있습니다.`,
    );
  }
  if (values.enabled && source.status !== "ACTIVE") {
    out.push("수집원 상태가 '정상(ACTIVE)'이 아니어서 자료 종류가 활성이어도 배치가 실행하지 않습니다.");
  }
  return out;
}

function withWarnings(message: string, warnings: string[]): ActionState {
  return {
    ok: true,
    message: warnings.length ? `${message} 주의: ${warnings.join(" / ")}` : message,
  };
}

/**
 * 자료 종류 추가 — 새 수집원을 만들었거나 자료 종류를 따로 나눌 때만 쓴다
 * (검색어별로 자료 종류를 만들던 방식은 없앴다: 검색어는 공용 목록이 담당).
 */
export async function createReportChannel(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const profile = await requireOperator();
  const sourceId = uuidField(formData, "source_id");
  if (!sourceId) return fail("대상 수집원을 찾을 수 없습니다.");
  const parsed = parseChannelFields(formData);
  if ("error" in parsed) return fail(parsed.error);

  const supabase = createServiceRoleClient();
  const { data: source } = await supabase
    .from("report_sources")
    .select("source_key, status, adapter_key")
    .eq("id", sourceId)
    .maybeSingle();
  if (!source) return fail("대상 수집원을 찾을 수 없습니다.");

  const { data, error } = await supabase
    .from("report_source_channels")
    .insert({ source_id: sourceId, ...parsed.values, retired_at: null })
    .select("id")
    .single();
  if (error || !data) {
    return fail(
      dbErrorMessage(error ?? { message: "알 수 없는 오류" }, `이 수집원에 자료 종류 키 '${parsed.values.channel_key}'가 이미 있습니다.`),
    );
  }

  await logEvent(
    supabase, "REPORT_CHANNEL_CREATE", "report_source_channels", data.id, profile.id,
    `${source.source_key}/${parsed.values.channel_key} · ${parsed.values.name}`,
    {
      source_id: sourceId,
      uses_keywords: parsed.values.uses_keywords,
      credential_key_name: parsed.values.credential_key_name,
    },
  );
  revalidatePath(PAGE_PATH);
  return withWarnings(
    `자료 종류 '${parsed.values.name}'을(를) 추가했습니다. 다음 수집부터 실행 대상이 됩니다.`,
    channelWarnings(parsed.values, source, await countReportKeywords(supabase)),
  );
}

/** 자료 종류 편집 (전 항목). 은퇴한 옛 채널은 기록 보존용이라 편집하지 않는다. */
export async function updateReportChannel(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const profile = await requireOperator();
  const id = uuidField(formData, "id");
  if (!id) return fail("대상 자료 종류를 찾을 수 없습니다.");
  const parsed = parseChannelFields(formData);
  if ("error" in parsed) return fail(parsed.error);

  const supabase = createServiceRoleClient();
  const { data: before, error: beforeError } = await supabase
    .from("report_source_channels")
    .select("source_id, channel_key, enabled, uses_keywords, retired_at, credential_key_name")
    .eq("id", id)
    .maybeSingle();
  // 새 컬럼(uses_keywords·retired_at)을 함께 읽으므로 마이그레이션 26 전에는
  // '자료 종류를 못 찾음'이 아니라 조회 자체가 실패한다 — 원인을 그대로 알린다
  if (!before) {
    return fail(
      isMissingSchemaError(beforeError)
        ? MIGRATION_PENDING_MESSAGE
        : "대상 자료 종류를 찾을 수 없습니다.",
    );
  }
  if (before.retired_at) {
    return fail(
      "이 자료 종류는 검색 키워드로 통합된 옛 채널이라 수집 기록 보존용으로만 남아 있습니다. 편집할 수 없습니다.",
    );
  }
  const { data: source } = await supabase
    .from("report_sources")
    .select("source_key, status, adapter_key")
    .eq("id", before.source_id)
    .maybeSingle();
  if (!source) return fail("연결된 수집원을 찾을 수 없습니다.");

  const { error } = await supabase
    .from("report_source_channels")
    .update({ ...parsed.values, updated_at: new Date().toISOString() })
    .eq("id", id);
  if (error) {
    return fail(dbErrorMessage(error, `이 수집원에 자료 종류 키 '${parsed.values.channel_key}'가 이미 있습니다.`));
  }

  await logEvent(
    supabase, "REPORT_CHANNEL_UPDATE", "report_source_channels", id, profile.id,
    `${source.source_key}/${parsed.values.channel_key} · ${parsed.values.name}`,
    {
      enabled: { from: before.enabled, to: parsed.values.enabled },
      uses_keywords: { from: before.uses_keywords, to: parsed.values.uses_keywords },
      credential_key_name: { from: before.credential_key_name, to: parsed.values.credential_key_name },
    },
  );
  revalidatePath(PAGE_PATH);
  return withWarnings("저장했습니다.", channelWarnings(parsed.values, source, await countReportKeywords(supabase)));
}

/**
 * 자료 종류 정리 — 수집원 삭제와 같은 원칙으로, 이 자료 종류로 모은 자료가 있으면
 * 지우지 않고 '수집 기록 보존용'으로 물린다(enabled=false, retired_at=now()).
 * 자료가 0건일 때만 실제로 지운다.
 */
export async function deleteReportChannel(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const profile = await requireOperator();
  const id = uuidField(formData, "id");
  if (!id) return fail("대상 자료 종류를 찾을 수 없습니다.");

  const supabase = createServiceRoleClient();
  const [{ data: channel }, { count: occurrences }] = await Promise.all([
    supabase
      .from("report_source_channels")
      .select("source_id, channel_key, name, retired_at")
      .eq("id", id)
      .maybeSingle(),
    supabase.from("report_occurrences").select("id", { count: "exact", head: true }).eq("channel_id", id),
  ]);
  if (!channel) return fail("대상 자료 종류를 찾을 수 없습니다.");
  if (channel.retired_at) {
    return fail("이미 정리된 옛 채널입니다. 수집 기록 보존용으로 그대로 두세요.");
  }

  const kept = occurrences ?? 0;
  if (kept > 0) {
    const { error } = await supabase
      .from("report_source_channels")
      .update({
        enabled: false,
        retired_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      })
      .eq("id", id);
    if (error) return fail(`정리에 실패했습니다: ${error.message}`);

    await logEvent(
      supabase, "REPORT_CHANNEL_DELETE", "report_source_channels", id, profile.id,
      `${channel.channel_key} · ${channel.name} (자료 ${kept}건 — 삭제 대신 수집 기록 보존용으로 전환)`,
      { source_id: channel.source_id, channel_key: channel.channel_key, occurrence_count: kept, soft: true },
    );
    revalidatePath(PAGE_PATH);
    return {
      ok: true,
      message:
        `자료 종류 '${channel.name}'을(를) 더 이상 수집하지 않습니다. 이미 모은 자료 ${kept.toLocaleString("ko-KR")}건이 있어 ` +
        "지우지 않고 화면 아래 '검색 키워드로 통합된 옛 채널'로 옮겼습니다.",
    };
  }

  const { error } = await supabase.from("report_source_channels").delete().eq("id", id);
  if (error) return fail(`삭제에 실패했습니다: ${error.message}`);

  await logEvent(
    supabase, "REPORT_CHANNEL_DELETE", "report_source_channels", id, profile.id,
    `${channel.channel_key} · ${channel.name} (모은 자료 없음 — 실제 삭제)`,
    { source_id: channel.source_id, channel_key: channel.channel_key, occurrence_count: 0, soft: false },
  );
  revalidatePath(PAGE_PATH);
  return { ok: true, message: `자료 종류 '${channel.name}'을(를) 삭제했습니다. (모은 자료 없음)` };
}

/** 자료 종류 활성·비활성 토글. */
export async function toggleReportChannel(formData: FormData) {
  const profile = await requireOperator();
  const id = uuidField(formData, "id");
  if (!id) return;
  const enable = String(formData.get("enable")) === "true";

  const supabase = createServiceRoleClient();
  const { data: channel } = await supabase
    .from("report_source_channels")
    .select("channel_key, retired_at")
    .eq("id", id)
    .maybeSingle();
  // 은퇴한 옛 채널은 다시 켜지 않는다 — 켜면 옛 검색어로 중복 수집이 되살아난다
  if (!channel || channel.retired_at) return;

  const { error } = await supabase
    .from("report_source_channels")
    .update({ enabled: enable, updated_at: new Date().toISOString() })
    .eq("id", id);
  // 실패해도 예외가 아니라 error로 돌아온다 — 그냥 두면 켜지지 않았는데도
  // 이력에는 '활성화'가 남는다. 화면은 다음 로드에서 실제 상태를 보여준다.
  if (error) {
    console.error("[toggleReportChannel] update 실패", {
      id,
      enable,
      code: error.code,
      message: error.message,
    });
    return;
  }
  await logEvent(
    supabase,
    enable ? "REPORT_CHANNEL_ENABLE" : "REPORT_CHANNEL_DISABLE",
    "report_source_channels",
    id,
    profile.id,
    channel.channel_key,
  );
  revalidatePath(PAGE_PATH);
}

/**
 * '지금 실행 대상으로' — 이 자료 종류의 (자료 종류 × 검색어) 조합 실행 상태를 지워
 * 다음 배치(fetch_due_tasks)에서 주기와 무관하게 전 조합이 실행되게 한다.
 * 배치는 report_channel_keyword_runs 로 주기를 판정하므로 그 행을 지우지
 * 않으면 last_run_at 만 비워도 '지금 실행'이 먹지 않는다. 화면 표시용
 * channels.last_run_at 도 함께 비운다.
 */
export async function resetReportChannelRun(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const profile = await requireOperator();
  const id = uuidField(formData, "id");
  if (!id) return fail("대상 자료 종류를 찾을 수 없습니다.");

  const supabase = createServiceRoleClient();
  const { data: channel, error: channelError } = await supabase
    .from("report_source_channels")
    .select("source_id, channel_key, enabled, retired_at")
    .eq("id", id)
    .maybeSingle();
  // retired_at은 마이그레이션 26이 만드는 컬럼이다 — 적용 전에는 조회가 실패한다
  if (!channel) {
    return fail(
      isMissingSchemaError(channelError)
        ? MIGRATION_PENDING_MESSAGE
        : "대상 자료 종류를 찾을 수 없습니다.",
    );
  }
  if (channel.retired_at) {
    return fail("이 자료 종류는 검색 키워드로 통합된 옛 채널이라 다시 실행하지 않습니다.");
  }
  const { data: source } = await supabase
    .from("report_sources")
    .select("source_key, status")
    .eq("id", channel.source_id)
    .maybeSingle();

  // 조합 상태 표도 마이그레이션 26이 만든다 — 아직 없으면 지울 것도 없으므로
  // 여기서 멈추지 말고 화면 표시용 last_run_at 초기화까지는 마친다
  const { error: runsError } = await supabase
    .from("report_channel_keyword_runs")
    .delete()
    .eq("channel_id", id);
  const runsTableMissing = isMissingSchemaError(runsError);
  if (runsError && !runsTableMissing) {
    return fail(`처리에 실패했습니다: ${runsError.message}`);
  }

  const { error } = await supabase
    .from("report_source_channels")
    .update({ last_run_at: null, updated_at: new Date().toISOString() })
    .eq("id", id);
  if (error) return fail(`처리에 실패했습니다: ${error.message}`);

  await logEvent(
    supabase, "REPORT_CHANNEL_RESET_RUN", "report_source_channels", id, profile.id,
    `${source?.source_key ?? "?"}/${channel.channel_key}: 조합 실행 상태 초기화`,
  );
  revalidatePath(PAGE_PATH);

  const warnings: string[] = [];
  if (runsTableMissing) {
    warnings.push(
      "검색어별 실행 기록 표가 아직 데이터베이스에 없어 자료 종류의 '마지막 실행' 시각만 비웠습니다",
    );
  }
  if (!channel.enabled) warnings.push("자료 종류가 비활성이라 켜기 전에는 실행되지 않습니다");
  if (source && source.status !== "ACTIVE") warnings.push("수집원 상태가 '정상(ACTIVE)'이 아니라 실행되지 않습니다");
  if (source?.source_key === "point") {
    warnings.push("POINT는 GitHub Actions에서 건너뛰므로(REPORT_SKIP_SOURCES) 사무실 PC 예약 실행에서만 돕니다");
  }
  return withWarnings(
    "다음 보고서 수집(매일 06:35, 또는 '지금 실행')에서 이 자료 종류를 검색어마다 한 번씩 실행합니다. " +
      "한 번에 다 못 돌면 남은 조합은 그다음 실행으로 넘어갑니다.",
    warnings,
  );
}

// ------------------------------------------------------------
// 보고서 수집 워크플로 즉시 실행
// ------------------------------------------------------------

/**
 * 보고서 수집 지금 실행 — collect-reports 워크플로(수집 → Gemini 분석) 트리거.
 * useActionState용이지만 이전 상태·폼 값은 쓰지 않으므로 인자를 받지 않는다.
 */
export async function triggerCollectReportsNow(): Promise<ActionState> {
  const profile = await requireOperator();

  if (await isWorkflowBusy(COLLECT_REPORTS_WORKFLOW)) {
    return fail(
      "보고서 수집 배치가 아직 실행 중입니다. 끝난 뒤 다시 누르세요 — 지금 누르면 뒤 실행이 " +
        "앞 실행을 기다리다 취소되고 실행 시간만 소모됩니다. (운영 > 사용량 표에서 RUNNING 여부를 볼 수 있습니다)",
    );
  }
  try {
    await dispatchWorkflow(COLLECT_REPORTS_WORKFLOW);
  } catch (e) {
    return fail(e instanceof Error ? e.message : "워크플로 실행 요청에 실패했습니다.");
  }

  const supabase = createServiceRoleClient();
  await logEvent(
    supabase, "OTHER", "report_sources", null, profile.id,
    "보고서 수집 수동 실행 (workflow_dispatch collect-reports)",
  );
  revalidatePath(PAGE_PATH);
  return {
    ok: true,
    message:
      "실행을 요청했습니다. 30초~2분 내 시작되며, 주기가 지난 (자료 종류 × 검색어) 조합만 정해진 시간 안에서 " +
      "수집하고 이어서 보고서 AI 분석도 돕니다. 한 번에 다 못 돌면 남은 조합은 다음 실행으로 넘어갑니다. " +
      "결과는 자료 종류의 '마지막 실행' 시각으로 확인하세요.",
  };
}
