"use server";

import { lookup } from "node:dns/promises";
import { isIP } from "node:net";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { requireOperator } from "@/lib/auth";
import { createServiceRoleClient } from "@/lib/supabase/server";

import {
  CREATABLE_FETCH_METHODS,
  DEFAULT_FETCH_INTERVAL,
  DEFAULT_PRIORITY,
  FETCH_METHOD_LABELS,
  SOURCE_TYPES,
  isKeywordManaged,
  type FetchMethod,
  type SourceRow,
} from "./source-fields";

/**
 * 검색 키워드 화면이 소유하는 행(managed_by='keyword_search')을 이 화면에서
 * 고치거나 지우려 할 때 돌려주는 안내. 이름·주소·검색어는 다음 수집 때
 * 배치가 검색어 목록을 보고 다시 계산하므로, 여기서 고쳐도 되돌아간다.
 */
const MANAGED_ROW_MESSAGE =
  "이 수집원은 [검색 키워드] 화면에서 관리합니다. 검색어·우선순위·수집 간격은 그 화면에서 바꿔 주세요.";

/** 폼(useActionState)에 돌려주는 결과. tone=warn은 실패는 아니지만 주의 표시. */
export type SourceActionResult = {
  ok: boolean;
  message: string;
  details?: string[];
  tone?: "warn";
};

type Supabase = ReturnType<typeof createServiceRoleClient>;

function fail(message: string, details?: string[]): SourceActionResult {
  return { ok: false, message, details };
}

// ------------------------------------------------------------
// 운영 조치 이력
// ------------------------------------------------------------

/**
 * operation_events.event_type에는 CHECK 제약(고정 목록)이 있어 SOURCE_CREATE 등
 * 새 유형이 아직 허용되지 않을 수 있다. 제약 위반(23514)이면 'OTHER'로 다시
 * 기록하되 의도한 유형을 reason·detail에 남긴다 — 기록이 조용히 사라지지 않게.
 */
async function logSourceEvent(
  supabase: Supabase,
  eventType: "SOURCE_CREATE" | "SOURCE_UPDATE" | "SOURCE_DELETE" | "SOURCE_DISABLE",
  targetId: string,
  actorId: string,
  reason: string,
) {
  const row = {
    event_type: eventType,
    target_table: "sources",
    target_id: targetId,
    actor_id: actorId,
    reason,
    detail: { intended_event_type: eventType },
  };
  const { error } = await supabase.from("operation_events").insert(row);
  if (error && error.code === "23514") {
    await supabase
      .from("operation_events")
      .insert({ ...row, event_type: "OTHER", reason: `${eventType} — ${reason}` });
  }
}

// ------------------------------------------------------------
// 입력 파싱·검증
// ------------------------------------------------------------

function str(formData: FormData, key: string, max: number): string {
  return String(formData.get(key) ?? "").trim().slice(0, max);
}

function int(formData: FormData, key: string, fallback: number): number {
  const n = Number.parseInt(String(formData.get(key) ?? ""), 10);
  return Number.isFinite(n) ? n : fallback;
}

/** 사설·예약 IPv4 대역 (net_guard.py의 ipaddress 판정과 같은 범위). */
function isPrivateIpv4(ip: string): boolean {
  const [a, b] = ip.split(".").map(Number);
  return (
    a === 0 || a === 10 || a === 127 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    (a === 198 && (b === 18 || b === 19)) ||
    a >= 224
  );
}

/** IPv4·IPv6 주소가 사설·예약 대역인지. IPv4 매핑(::ffff:…)도 풀어서 본다. */
function isPrivateIp(ip: string): boolean {
  if (isIP(ip) === 4) return isPrivateIpv4(ip);
  const v6 = ip.toLowerCase();
  if (v6.startsWith("::ffff:")) return isPrivateIp(v6.slice(7));
  return (
    v6 === "::" || v6 === "::1" ||
    /^f[cd]/.test(v6) || // fc00::/7 유니크 로컬
    /^fe[89ab]/.test(v6) || // fe80::/10 링크 로컬
    v6.startsWith("ff") // ff00::/8 멀티캐스트
  );
}

/**
 * 비공개 대상 URL 1차 차단 (리터럴 IP·내부 호스트명·포트). 'URL 확인'은 이
 * 서버에서 직접 fetch하므로 배치의 net_guard.py가 막아 주지 않는다 —
 * 도메인이 사설 IP로 풀리는 경우(예: 127.0.0.1.nip.io)는 여기서 걸리지 않으니
 * fetchLimited 안의 rejectPrivateResolution이 홉마다 DNS 해석까지 확인한다.
 */
function rejectNonPublicUrl(raw: string): string | null {
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return "URL 형식이 올바르지 않습니다.";
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") {
    return "http 또는 https로 시작하는 주소만 등록할 수 있습니다.";
  }
  if (u.username || u.password) {
    return "아이디·비밀번호가 포함된 URL은 등록할 수 없습니다.";
  }
  if (u.port && u.port !== "80" && u.port !== "443") {
    return "80·443 이외 포트는 등록할 수 없습니다.";
  }
  const host = u.hostname.toLowerCase();
  if (
    host === "localhost" ||
    host.endsWith(".localhost") ||
    host.endsWith(".local") ||
    host.endsWith(".internal") ||
    host.endsWith(".lan") ||
    host.endsWith(".home") ||
    host === "::1" ||
    host.startsWith("[") // IPv6 전체 — 공개 수집원에 쓸 일이 없다
  ) {
    return "내부 호스트 주소는 등록할 수 없습니다.";
  }
  const ipv4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.test(host);
  if (ipv4) {
    if (isPrivateIpv4(host)) {
      return "사설·예약 IP 주소는 등록할 수 없습니다.";
    }
  } else if (!host.includes(".")) {
    return "도메인 이름이 완전하지 않습니다 (예: example.com).";
  }
  return null;
}

function isGoogleNewsHost(url: string): boolean {
  try {
    return new URL(url).hostname.toLowerCase().endsWith("news.google.com");
  } catch {
    return false;
  }
}

function dbErrorMessage(error: { code?: string; message: string }): string {
  if (error.code === "23505") {
    return "같은 이름 또는 같은 URL의 수집원이 이미 있습니다.";
  }
  if (error.code === "23514") {
    return "허용되지 않는 값이 있습니다. 유형·수집 방식을 확인해 주세요.";
  }
  return `저장에 실패했습니다: ${error.message}`;
}

const FIELD_LABELS: Record<string, string> = {
  name: "이름",
  source_type: "유형",
  country_region: "지역",
  language: "언어",
  url: "URL",
  adapter_config: "선택자 설정",
  fetch_interval_minutes: "수집 간격",
  priority: "우선순위",
  notes: "메모",
};

// ------------------------------------------------------------
// 추가·편집
// ------------------------------------------------------------

/**
 * 수집원 추가(id 없음)·편집(id 있음). 편집 시 수집 방식은 기존 값을 유지한다 —
 * 방식이 바뀌면 adapter_config·URL 의미가 통째로 달라져 실수를 부르기 때문.
 *
 * 이 화면이 다루는 것은 RSS 피드·목록 페이지뿐이다 (사용자 요구 1-3).
 * 네이버·구글 뉴스 검색어 행은 검색 키워드 화면이 소유하므로 여기서 거부한다.
 */
export async function saveSource(
  _prev: SourceActionResult | null,
  formData: FormData,
): Promise<SourceActionResult> {
  const profile = await requireOperator();
  const supabase = createServiceRoleClient();

  const id = str(formData, "id", 64) || null;
  const name = str(formData, "name", 120);
  const sourceType = str(formData, "source_type", 50);
  const countryRegion = str(formData, "country_region", 40) || "국내";
  const language = str(formData, "language", 20).toLowerCase() || "ko";
  const notes = str(formData, "notes", 500) || null;
  const priority = int(formData, "priority", DEFAULT_PRIORITY);
  const interval = int(formData, "fetch_interval_minutes", DEFAULT_FETCH_INTERVAL);

  if (!name) return fail("이름을 입력해 주세요.");
  if (!(SOURCE_TYPES as readonly string[]).includes(sourceType)) {
    return fail("유형을 목록에서 선택해 주세요.");
  }
  if (priority < 1 || priority > 100) {
    return fail("우선순위는 1~100 사이의 정수여야 합니다 (낮을수록 수집 순서 우선).");
  }
  if (interval < 10 || interval > 1440) {
    return fail("수집 간격은 10~1440분 사이여야 합니다.");
  }

  let existing: SourceRow | null = null;
  if (id) {
    const { data } = await supabase
      .from("sources")
      .select("*")
      .eq("id", id)
      .maybeSingle();
    if (!data) {
      return fail("편집할 수집원을 찾을 수 없습니다. 목록을 새로고침해 주세요.");
    }
    existing = data as SourceRow;
    if (isKeywordManaged(existing)) return fail(MANAGED_ROW_MESSAGE);
  }
  const fetchMethod: FetchMethod = existing
    ? existing.fetch_method
    : (str(formData, "fetch_method", 20) as FetchMethod);
  if (!existing) {
    if (fetchMethod === "NAVER_API") {
      return fail(
        "네이버 뉴스 검색은 [검색 키워드] 화면에서 검색어를 추가하는 방식으로 등록합니다. 여기서는 RSS 피드·목록 페이지만 추가할 수 있습니다.",
      );
    }
    if (!CREATABLE_FETCH_METHODS.includes(fetchMethod)) {
      return fail("수집 방식을 선택해 주세요 (RSS 피드 또는 목록 페이지).");
    }
  }

  let url: string;
  let adapterConfig: Record<string, unknown> | null;

  if (fetchMethod === "RSS") {
    url = str(formData, "url", 2000);
    const blocked = rejectNonPublicUrl(url);
    if (blocked) return fail(blocked);
    const cfg: Record<string, unknown> = { ...(existing?.adapter_config ?? {}) };
    // 구글 뉴스 RSS는 링크 해독 없이는 원문을 못 가져오므로 자동으로 켠다
    if (formData.get("link_decoder") === "on" || isGoogleNewsHost(url)) {
      cfg.link_decoder = "google_news";
    } else {
      delete cfg.link_decoder;
    }
    adapterConfig = Object.keys(cfg).length ? cfg : null;
  } else if (fetchMethod === "LIST_PAGE") {
    url = str(formData, "url", 2000);
    const blocked = rejectNonPublicUrl(url);
    if (blocked) return fail(blocked);
    const itemSelector = str(formData, "item_selector", 300);
    if (!itemSelector) {
      return fail("항목 선택자(item_selector)를 입력해 주세요. 목록 페이지 수집에 꼭 필요합니다.");
    }
    const titleSelector = str(formData, "title_selector", 200);
    const summarySelector = str(formData, "summary_selector", 200);
    const baseUrl = str(formData, "base_url", 2000);
    if (baseUrl) {
      const blockedBase = rejectNonPublicUrl(baseUrl);
      if (blockedBase) return fail(`기준 URL: ${blockedBase}`);
    }
    const cfg: Record<string, unknown> = {
      ...(existing?.adapter_config ?? {}),
      item_selector: itemSelector,
    };
    if (titleSelector) cfg.title_selector = titleSelector;
    else delete cfg.title_selector;
    if (summarySelector) cfg.summary_selector = summarySelector;
    else delete cfg.summary_selector;
    if (baseUrl) cfg.base_url = baseUrl;
    else delete cfg.base_url;
    adapterConfig = cfg;
  } else {
    // PREDEFINED(코드 어댑터)와 아직 태깅되지 않은 옛 NAVER_API 행.
    // 주소·검색어 설정은 이 화면에서 바꾸지 않고 이름·유형·우선순위만 고친다.
    if (!existing) {
      return fail("이 수집 방식은 화면에서 추가할 수 없습니다.");
    }
    url = existing.url;
    adapterConfig = existing.adapter_config;
  }

  // 이름·URL 중복 검사 (DB UNIQUE는 url에만 있어 이름은 여기서만 막힌다)
  // 조회가 실패하면 '중복 없음'으로 새지 않도록 저장을 중단한다.
  // 이름은 PostgREST 패턴 비교(ilike)를 쓰지 않는다 — '*'가 '%'로 해석되는데
  // 이스케이프할 방법이 없어 ‘로봇*’ 같은 이름이 기존 행과 잘못 겹친다.
  // 수집원은 수십 행뿐이라 받아서 비교해도 부담이 없다.
  let nameQuery = supabase.from("sources").select("id, name");
  if (existing) nameQuery = nameQuery.neq("id", existing.id);
  const { data: allNames, error: nameError } = await nameQuery;
  if (nameError) {
    return fail(`이름 중복 확인에 실패했습니다: ${nameError.message}`);
  }
  const wantedName = name.toLocaleLowerCase();
  const nameDup = (allNames ?? []).find(
    (r: { name: string }) => r.name.toLocaleLowerCase() === wantedName,
  );
  if (nameDup) {
    return fail(`같은 이름의 수집원이 이미 있습니다: ${nameDup.name}`);
  }
  let urlQuery = supabase.from("sources").select("id, name").eq("url", url).limit(1);
  if (existing) urlQuery = urlQuery.neq("id", existing.id);
  const { data: urlDup, error: urlError } = await urlQuery;
  if (urlError) {
    return fail(`주소 중복 확인에 실패했습니다: ${urlError.message}`);
  }
  if (urlDup && urlDup.length > 0) {
    return fail(`같은 URL의 수집원이 이미 있습니다: ${urlDup[0].name}`);
  }

  const payload = {
    name,
    source_type: sourceType,
    country_region: countryRegion,
    language,
    url,
    fetch_method: fetchMethod,
    adapter_config: adapterConfig,
    fetch_interval_minutes: interval,
    priority,
    notes,
  };

  if (existing) {
    const changed = (Object.keys(payload) as (keyof typeof payload)[])
      .filter(
        (k) =>
          JSON.stringify(payload[k] ?? null) !==
          JSON.stringify(existing[k] ?? null),
      )
      .map((k) => FIELD_LABELS[k] ?? k);
    const { error } = await supabase
      .from("sources")
      .update({ ...payload, updated_at: new Date().toISOString() })
      .eq("id", existing.id);
    if (error) return fail(dbErrorMessage(error));
    await logSourceEvent(
      supabase,
      "SOURCE_UPDATE",
      existing.id,
      profile.id,
      changed.length
        ? `${name} 수정 — ${changed.join(", ")}`
        : `${name} 수정 — 변경 없음`,
    );
    revalidatePath("/admin/sources");
    return {
      ok: true,
      message: changed.length
        ? `저장했습니다 (${changed.join(", ")}). 다음 수집부터 반영됩니다.`
        : "바뀐 내용이 없습니다.",
    };
  }

  const { data: inserted, error } = await supabase
    .from("sources")
    .insert({ ...payload, is_active: true })
    .select("id")
    .single();
  if (error || !inserted) {
    return fail(error ? dbErrorMessage(error) : "추가에 실패했습니다.");
  }
  await logSourceEvent(
    supabase,
    "SOURCE_CREATE",
    inserted.id,
    profile.id,
    `${name} 추가 — ${FETCH_METHOD_LABELS[fetchMethod]} · ${url}`,
  );
  revalidatePath("/admin/sources");
  // 첫 수집은 수집 간격과 무관하다 — collect.py는 last_success_at이 비어 있는
  // 수집원을 간격과 관계없이 바로 대상에 넣으므로, 실제 대기 시간은 다음 정기
  // 배치까지다. '최대 N분 안'이라고 적으면 그 시간이 지나도 기록이 없을 때
  // 고장으로 오인해 재등록·수동 실행을 반복하게 된다.
  return {
    ok: true,
    message:
      `‘${name}’을(를) 추가했습니다. 다음 정기 수집 배치(하루 3회)부터 반영됩니다. ` +
      "바로 반영하려면 위의 ‘지금 수집 실행’을 누르고, 완료 후 ‘마지막 성공’ 시각으로 확인해 주세요.",
  };
}

// ------------------------------------------------------------
// 삭제
// ------------------------------------------------------------

/**
 * 수집 기록(raw_items)이 없으면 하드 삭제, 있으면 비활성화.
 * raw_items.source_id는 ON DELETE SET NULL이라 삭제 자체는 막히지 않지만,
 * 그러면 기사가 어느 수집원에서 왔는지 알 수 없게 되므로 기록이 있으면 남긴다.
 */
export async function deleteSource(
  _prev: SourceActionResult | null,
  formData: FormData,
): Promise<SourceActionResult> {
  const profile = await requireOperator();
  const id = str(formData, "id", 64);
  if (!id) return fail("삭제할 수집원이 지정되지 않았습니다.");

  const supabase = createServiceRoleClient();
  // '*'로 읽는다 — managed_by 컬럼은 마이그레이션 적용 전에는 없다.
  const { data: rowData } = await supabase
    .from("sources")
    .select("*")
    .eq("id", id)
    .maybeSingle();
  if (!rowData) return fail("이미 삭제됐거나 찾을 수 없는 수집원입니다.");
  const row = rowData as SourceRow;
  if (isKeywordManaged(row)) {
    return fail(
      `${MANAGED_ROW_MESSAGE} 검색어를 지우면 이 수집원은 다음 수집 때 자동으로 꺼집니다.`,
    );
  }

  const { count, error: countError } = await supabase
    .from("raw_items")
    .select("id", { count: "exact", head: true })
    .eq("source_id", id);
  if (countError) {
    return fail(`수집 기록을 확인하지 못했습니다: ${countError.message}`);
  }
  const rawCount = count ?? 0;

  if (rawCount > 0) {
    const { error } = await supabase
      .from("sources")
      .update({ is_active: false, updated_at: new Date().toISOString() })
      .eq("id", id);
    if (error) return fail(`비활성화에 실패했습니다: ${error.message}`);
    await logSourceEvent(
      supabase,
      "SOURCE_DISABLE",
      id,
      profile.id,
      `삭제 요청 — 수집 기록 ${rawCount}건이 있어 비활성화 (${row.name})`,
    );
    revalidatePath("/admin/sources");
    // 안내 문구는 화면이 조립한다 — 주소창의 자유 문자열을 그대로 성공 배너에
    // 띄우면 링크 하나로 운영자에게 가짜 시스템 메시지를 보여줄 수 있다.
    redirect(`/admin/sources?done=disabled&id=${encodeURIComponent(id)}`);
  }

  const { error } = await supabase.from("sources").delete().eq("id", id);
  if (error) return fail(`삭제에 실패했습니다: ${error.message}`);
  await logSourceEvent(
    supabase,
    "SOURCE_DELETE",
    id,
    profile.id,
    `${row.name} 삭제 (수집 기록 0건)`,
  );
  revalidatePath("/admin/sources");
  redirect(`/admin/sources?done=deleted&name=${encodeURIComponent(row.name)}`);
}

// ------------------------------------------------------------
// URL 확인 (RSS·목록 페이지)
// ------------------------------------------------------------

const CHECK_TIMEOUT_MS = 10_000;
const CHECK_MAX_BYTES = 3 * 1024 * 1024;
const CHECK_MAX_REDIRECTS = 5;

type FetchedPage = {
  status: number;
  contentType: string;
  finalUrl: string;
  text: string;
  bytes: number;
  truncated: boolean;
};

async function readLimited(res: Response, max: number) {
  const reader = res.body?.getReader();
  if (!reader) return { text: "", bytes: 0, truncated: false };
  const chunks: Uint8Array[] = [];
  let total = 0;
  let truncated = false;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (!value) continue;
    const room = max - total;
    if (value.byteLength > room) {
      chunks.push(value.subarray(0, room));
      total += room;
      truncated = true;
      await reader.cancel();
      break;
    }
    chunks.push(value);
    total += value.byteLength;
  }
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    merged.set(c, offset);
    offset += c.byteLength;
  }
  return {
    text: new TextDecoder("utf-8", { fatal: false }).decode(merged),
    bytes: total,
    truncated,
  };
}

/**
 * 호스트명이 사설·예약 대역으로 해석되면 사유를 돌려준다 (net_guard.py와 같은 취지).
 * 리터럴 IP 검사만으로는 127.0.0.1.nip.io처럼 공개 도메인이 내부 주소로 풀리는
 * 경우를 막지 못해, 이 서버가 내부망을 대신 조회하는 통로가 된다.
 */
async function rejectPrivateResolution(url: string): Promise<string | null> {
  const host = new URL(url).hostname.replace(/^\[|\]$/g, "");
  if (isIP(host)) {
    return isPrivateIp(host) ? `비공개 주소(${host})입니다.` : null;
  }
  let addrs: { address: string }[];
  try {
    addrs = await lookup(host, { all: true });
  } catch {
    return "DNS 해석에 실패했습니다.";
  }
  if (addrs.length === 0) return "DNS 해석 결과가 없습니다.";
  const bad = addrs.find((a) => isPrivateIp(a.address));
  return bad ? `비공개 주소(${bad.address})로 해석되는 호스트입니다.` : null;
}

/** 리다이렉트를 직접 따라가며 매 단계 공개 URL인지 검사한다 (SSRF 방지). */
async function fetchLimited(startUrl: string): Promise<FetchedPage> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CHECK_TIMEOUT_MS);
  try {
    let url = startUrl;
    for (let hop = 0; hop <= CHECK_MAX_REDIRECTS; hop++) {
      const resolved = await rejectPrivateResolution(url);
      if (resolved) {
        throw new Error(`허용되지 않는 주소입니다 (${url}): ${resolved}`);
      }
      const res = await fetch(url, {
        redirect: "manual",
        signal: controller.signal,
        cache: "no-store",
        headers: {
          "User-Agent": "KIRO-RobotIntel/0.1 (internal research bot; source check)",
          Accept:
            "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.5",
        },
      });
      const location = res.headers.get("location");
      if (res.status >= 300 && res.status < 400 && location) {
        const next = new URL(location, url).toString();
        const blocked = rejectNonPublicUrl(next);
        if (blocked) {
          throw new Error(`리다이렉트된 주소가 허용되지 않습니다 (${next}): ${blocked}`);
        }
        await res.body?.cancel();
        url = next;
        continue;
      }
      const body = await readLimited(res, CHECK_MAX_BYTES);
      return {
        status: res.status,
        contentType: res.headers.get("content-type") ?? "",
        finalUrl: url,
        ...body,
      };
    }
    throw new Error(`리다이렉트가 너무 많습니다 (${CHECK_MAX_REDIRECTS}회 초과).`);
  } finally {
    clearTimeout(timer);
  }
}

function describeError(e: unknown): string {
  if (e instanceof Error) {
    if (e.name === "AbortError") return `${CHECK_TIMEOUT_MS / 1000}초 안에 응답이 없습니다.`;
    const cause = (e as { cause?: { code?: string; message?: string } }).cause;
    return cause?.code ? `${e.message} (${cause.code})` : e.message;
  }
  return String(e);
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}

function countMatches(text: string, re: RegExp): number {
  return (text.match(re) ?? []).length;
}

// --- CSS 선택자 매칭 수 추정 (cheerio 없이 태그·속성 문자열 비교) ---

type AttrTest = { name: string; op: string; value: string };
type Compound = {
  tag: string | null;
  id: string | null;
  classes: string[];
  attrs: AttrTest[];
  unsupported: boolean;
};

/** 결합자(공백 > + ~ ,)로 나눈 마지막 복합 선택자만 쓴다 — 조상 조건은 못 본다. */
function lastCompound(selector: string): { compound: string; partial: boolean } {
  const parts: string[] = [];
  let cur = "";
  let depth = 0;
  let quote: string | null = null;
  for (const ch of selector) {
    if (quote) {
      cur += ch;
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      cur += ch;
      continue;
    }
    if (ch === "[") depth++;
    else if (ch === "]") depth--;
    if (depth === 0 && (ch === " " || ch === ">" || ch === "+" || ch === "~" || ch === ",")) {
      if (cur.trim()) parts.push(cur.trim());
      cur = "";
      continue;
    }
    cur += ch;
  }
  if (cur.trim()) parts.push(cur.trim());
  return { compound: parts[parts.length - 1] ?? selector.trim(), partial: parts.length > 1 };
}

function parseCompound(compound: string): Compound {
  const out: Compound = { tag: null, id: null, classes: [], attrs: [], unsupported: false };
  let rest = compound;
  const tagMatch = rest.match(/^([a-zA-Z][\w-]*|\*)/);
  if (tagMatch) {
    out.tag = tagMatch[1] === "*" ? null : tagMatch[1].toLowerCase();
    rest = rest.slice(tagMatch[0].length);
  }
  const re =
    /#([\w-]+)|\.([\w-]+)|\[\s*([\w:-]+)\s*(?:([~|^$*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+)))?\s*\]|(::?[\w-]+(?:\([^)]*\))?)/y;
  let pos = 0;
  while (pos < rest.length) {
    re.lastIndex = pos;
    const m = re.exec(rest);
    if (!m) {
      out.unsupported = true;
      break;
    }
    pos = re.lastIndex;
    if (m[1]) out.id = m[1];
    else if (m[2]) out.classes.push(m[2]);
    else if (m[3]) {
      out.attrs.push({
        name: m[3].toLowerCase(),
        op: m[4] ?? "",
        value: m[5] ?? m[6] ?? m[7] ?? "",
      });
    } else if (m[8]) out.unsupported = true; // :nth-child 등 가상 선택자는 못 본다
  }
  return out;
}

function parseAttrs(attrText: string): Map<string, string> {
  const map = new Map<string, string>();
  const re = /([^\s"'=<>/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(attrText))) {
    map.set(m[1].toLowerCase(), m[2] ?? m[3] ?? m[4] ?? "");
  }
  return map;
}

function attrMatches(actual: string | undefined, t: AttrTest): boolean {
  if (actual === undefined) return false;
  switch (t.op) {
    case "": return true;
    case "=": return actual === t.value;
    case "*=": return t.value !== "" && actual.includes(t.value);
    case "^=": return t.value !== "" && actual.startsWith(t.value);
    case "$=": return t.value !== "" && actual.endsWith(t.value);
    case "~=": return actual.split(/\s+/).includes(t.value);
    case "|=": return actual === t.value || actual.startsWith(`${t.value}-`);
    default: return false;
  }
}

function estimateSelectorMatches(html: string, selector: string) {
  const { compound, partial } = lastCompound(selector);
  const parsed = parseCompound(compound);
  const tagRe = /<([a-zA-Z][\w:-]*)\b([^>]*)>/g;
  let count = 0;
  let m: RegExpExecArray | null;
  while ((m = tagRe.exec(html))) {
    if (parsed.tag && m[1].toLowerCase() !== parsed.tag) continue;
    if (!parsed.id && parsed.classes.length === 0 && parsed.attrs.length === 0) {
      count++;
      continue;
    }
    const attrs = parseAttrs(m[2]);
    if (parsed.id && attrs.get("id") !== parsed.id) continue;
    if (parsed.classes.length) {
      const cls = (attrs.get("class") ?? "").split(/\s+/);
      if (!parsed.classes.every((c) => cls.includes(c))) continue;
    }
    if (!parsed.attrs.every((t) => attrMatches(attrs.get(t.name), t))) continue;
    count++;
  }
  return { count, compound, partial, unsupported: parsed.unsupported };
}

/**
 * 'URL 확인' — 저장 전에 주소가 살아 있는지, RSS면 항목이 있는지, 목록 페이지면
 * 선택자에 맞는 요소가 대략 몇 개인지 보여준다. 정확한 CSS 해석이 아니라
 * 추정치라는 점을 결과에 함께 적는다.
 */
export async function checkSourceUrl(
  _prev: SourceActionResult | null,
  formData: FormData,
): Promise<SourceActionResult> {
  await requireOperator();

  const fetchMethod = str(formData, "fetch_method", 20);
  if (fetchMethod === "NAVER_API") {
    return {
      ok: false,
      tone: "warn",
      message:
        "네이버 뉴스 검색은 [검색 키워드] 화면에서 관리하며, API 키가 이 서버(Vercel)에 없어 여기서는 미리 확인할 수 없습니다.",
    };
  }
  if (fetchMethod !== "RSS" && fetchMethod !== "LIST_PAGE") {
    return fail("URL 확인은 RSS 피드·목록 페이지에서만 할 수 있습니다.");
  }
  const url = str(formData, "url", 2000);
  if (!url) return fail("확인할 URL을 먼저 입력해 주세요.");
  const blocked = rejectNonPublicUrl(url);
  if (blocked) return fail(blocked);

  const started = Date.now();
  let page: FetchedPage;
  try {
    page = await fetchLimited(url);
  } catch (e) {
    return fail(`가져오지 못했습니다: ${describeError(e)}`);
  }
  const elapsed = Date.now() - started;

  const details = [
    `응답 코드 ${page.status}${page.finalUrl !== url ? ` (최종 주소: ${page.finalUrl})` : ""}`,
    `Content-Type: ${page.contentType || "(없음)"}`,
    `받은 크기 ${formatBytes(page.bytes)}${page.truncated ? " (3MB 상한에서 잘라 읽음)" : ""} · ${elapsed}ms`,
  ];
  if (page.status >= 400) {
    return fail(`서버가 오류를 돌려줬습니다 (HTTP ${page.status}). 주소를 다시 확인해 주세요.`, details);
  }

  const body = page.text;
  const contentType = page.contentType.toLowerCase();

  if (fetchMethod === "RSS") {
    const items = countMatches(body, /<item[\s>]/gi);
    const entries = countMatches(body, /<entry[\s>]/gi);
    const looksFeed = /<(rss|feed|rdf:RDF)[\s>]/i.test(body);
    const looksHtml = /<html[\s>]/i.test(body) || contentType.includes("text/html");
    const total = items + entries;
    details.push(`피드 항목: <item> ${items}개 · <entry> ${entries}개`);
    if (total === 0) {
      return fail(
        looksHtml && !looksFeed
          ? "RSS 피드가 아니라 일반 웹페이지로 보입니다. 사이트의 RSS 주소(보통 /rss, /feed 로 끝남)를 확인해 주세요."
          : "피드 형식이지만 항목이 하나도 없습니다. 주소를 다시 확인해 주세요.",
        details,
      );
    }
    if (isGoogleNewsHost(url)) {
      details.push(
        "구글 뉴스 RSS입니다 — 원문을 가져오려면 링크 해독이 필요하며, 저장 시 자동으로 켜집니다.",
      );
    }
    return { ok: true, message: `RSS 피드로 확인됐습니다 — 항목 ${total}개.`, details };
  }

  // LIST_PAGE
  const itemSelector = str(formData, "item_selector", 300);
  if (!itemSelector) {
    return {
      ok: false,
      tone: "warn",
      message: "페이지는 열렸지만 항목 선택자가 비어 있어 매칭 수를 셀 수 없습니다.",
      details,
    };
  }
  const est = estimateSelectorMatches(body, itemSelector);
  details.push(
    `항목 선택자 매칭 약 ${est.count}개${est.partial ? ` (마지막 부분 ‘${est.compound}’만 기준)` : ""}`,
  );
  for (const [key, label] of [
    ["title_selector", "제목 선택자"],
    ["summary_selector", "요약 선택자"],
  ] as const) {
    const sel = str(formData, key, 200);
    if (sel) {
      details.push(`${label} 매칭 약 ${estimateSelectorMatches(body, sel).count}개 (페이지 전체 기준)`);
    }
  }
  if (est.unsupported) {
    details.push("선택자에 이 확인이 해석하지 못하는 부분(가상 선택자 등)이 있어 수치가 부정확할 수 있습니다.");
  }
  details.push(
    "※ 이 확인은 태그·속성 문자열을 단순 비교한 추정치입니다. 실제 수집기는 CSS 선택자를 정확히 해석하므로 수치가 다를 수 있습니다.",
  );
  if (est.count === 0) {
    return fail("항목 선택자에 맞는 요소를 찾지 못했습니다. 선택자를 다시 확인해 주세요.", details);
  }
  return {
    ok: true,
    message: `페이지를 열었고 항목 선택자에 맞는 요소가 약 ${est.count}개 있습니다.`,
    details,
  };
}
