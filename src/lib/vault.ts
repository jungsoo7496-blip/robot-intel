import "server-only";

import { gunzipSync } from "node:zlib";

import { serverEnv } from "@/lib/env";

// ------------------------------------------------------------
// 금고(vault) 읽기 — Supabase Storage 비공개 버킷 'vault'.
//
// 배치(scripts/kiro_batch/vault.py)가 **반달(period)** 단위로 만든 파일을 읽는다.
//   period = 'YYYY-MM-a'(KST 1~15일) | 'YYYY-MM-b'(16일~말일)  — 계약 C1
//   items/YYYY-MM-a.jsonl.gz  레코드 1건 = 독립된 gzip 멤버 1개를 이어 붙인 파일
//   items/YYYY-MM-a.idx.json  {"<published_item_id>": [offset, length], ...}
// published_items.vaulted_at 이 있는 카드는 analyses 가 DB에서 지워졌으므로,
// 상세 화면은 여기서 부속(분석·분석 이력·관련 출처·정책)을 읽는다.
//
// 실패는 전부 VaultError 로 throw 한다 — 빈 값을 돌려주면 "0건"과 "장애"를
// 구분할 수 없다. 호출자(data.ts)가 잡아서 카드 정보만이라도 보여주고,
// `permanent` 로 '운영자에게 알릴 일'과 '잠시 후 다시'를 나눈다.
// ------------------------------------------------------------

const BUCKET = "vault";

/**
 * 실패 종류 — 화면 문구와 재시도 여부를 정한다.
 *  - input       published_at·period 키 형식 오류 (영구)
 *  - missing     색인·데이터 파일이 Storage 에 없음, HTTP 404 (영구)
 *  - index_miss  색인에 그 id 가 없음 (영구 — 색인을 새로 받아 한 번 재시도)
 *  - id_mismatch 레코드 id 가 기대와 다름 (영구 — 색인을 새로 받아 한 번 재시도)
 *  - corrupt     색인 구간·gzip·JSON·길이 불일치·416 (영구)
 *  - http        네트워크·5xx·429 등 일시 장애 (잠시 후 다시)
 */
export type VaultErrorKind =
  | "input"
  | "missing"
  | "index_miss"
  | "id_mismatch"
  | "corrupt"
  | "http";

/** 금고 읽기 실패 — 메시지는 운영자용 로그에 남고, 화면에는 안내 문구만 나간다. */
export class VaultError extends Error {
  readonly kind: VaultErrorKind;
  /** true 면 다시 시도해도 같은 결과 — 운영자가 봐야 한다. false 면 일시 장애. */
  readonly permanent: boolean;

  constructor(kind: VaultErrorKind, message: string) {
    super(message);
    this.name = "VaultError";
    this.kind = kind;
    this.permanent = kind !== "http";
  }
}

/**
 * 색인을 새로 받아 한 번 더 시도할 실패 종류 (계약 [39] + 재검증 지적).
 * index_miss·id_mismatch 외에 corrupt 도 넣는다 — 기간 파일이 다시 내보내져
 * 구간이 밀리면 낡은 색인으로 읽은 바이트가 gzip 조각이 아니게 되어 corrupt 로
 * 잡히기 때문이다. 새 색인으로 한 번 더 읽으면 흡수된다. http(일시)·missing
 * (파일 없음)은 재시도해도 같으므로 제외.
 */
function isRetryable(e: unknown): e is VaultError {
  return (
    e instanceof VaultError &&
    (e.kind === "index_miss" || e.kind === "id_mismatch" || e.kind === "corrupt")
  );
}

/** analyses 행 전체 컬럼 (supabase/migrations 0002 + 0011 기준). */
export type AnalysisRow = {
  id: string;
  cluster_id: string;
  model_name: string;
  prompt_version: string;
  schema_version: string;
  is_robot_related: boolean;
  category: string | null;
  region: string | null;
  robot_field: string | null;
  importance: string | null;
  evidence_level: string | null;
  kiro_relevance: string | null;
  display_title: string | null;
  one_line_summary: string | null;
  verified_facts: unknown;
  numbers_and_dates: unknown;
  ai_interpretation: string | null;
  kiro_implication: string | null;
  limitations: string | null;
  keywords: unknown;
  policy_meta: unknown;
  raw_response: unknown;
  input_token_count: number | null;
  output_token_count: number | null;
  generated_at: string;
  validation_status: string;
  kiro_relevance_axes: unknown;
  kiro_relevance_reason: string | null;
  kiro_watchpoints: unknown;
  kiro_context_version: string | null;
};

/** policy_details 행 전체 컬럼. */
export type PolicyDetailRow = {
  published_item_id: string;
  policy_name: string | null;
  project_name: string | null;
  ministries: string[] | null;
  organizations: string[] | null;
  budget_text: string | null;
  budget_amount_krw: number | null;
  project_start_date: string | null;
  project_end_date: string | null;
  support_targets: string[] | null;
  announcement_status: string | null;
  application_deadline: string | null;
  target_region: string | null;
  created_at: string;
  updated_at: string;
};

export type VaultRelatedSource = {
  is_representative: boolean;
  title: string | null;
  url: string;
  published_at: string | null;
  source_name: string | null;
};

/** 금고 레코드 (JSONL 한 줄) — 설계 §2 키 순서 + 계약 C2(analysis_history). */
export type VaultRecord = {
  id: string;
  cluster_id: string;
  published_at: string;
  display_date: string;
  is_visible: boolean;
  title: string;
  /** 내보낼 당시의 current 분석 */
  analysis: AnalysisRow | null;
  /** 같은 클러스터의 current 가 아닌 분석 전부 (계약 C2). 화면은 아직 그리지 않는다. */
  analysis_history?: AnalysisRow[];
  related_sources: VaultRelatedSource[];
  body: string | null;
  policy: PolicyDetailRow | null;
};

/** idx.json — published_item_id → [offset, length] (바이트 구간). */
export type VaultIndex = Record<string, [number, number]>;

const PERIOD_RE = /^\d{4}-\d{2}-[ab]$/;

/**
 * 반달 period 키 = published_at 을 Asia/Seoul 로 바꿔 YYYY-MM-a(1~15일) |
 * YYYY-MM-b(16일~말일) (계약 C1). 서울은 서머타임이 없으므로 +9시간 고정 계산으로
 * 충분하다 (item-card.tsx 와 동일).
 */
export function vaultPeriodKey(publishedAt: string): string {
  const t = new Date(publishedAt).getTime();
  if (Number.isNaN(t)) {
    throw new VaultError(
      "input",
      `published_at 을 해석할 수 없습니다: ${publishedAt}`,
    );
  }
  const d = new Date(t + 9 * 60 * 60 * 1000);
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const half = d.getUTCDate() <= 15 ? "a" : "b";
  return `${d.getUTCFullYear()}-${mm}-${half}`;
}

/**
 * idx 에서 한 항목의 바이트 구간을 꺼낸다. 없거나 모양이 틀리면 throw.
 * (순수 함수 — 단위 테스트용으로 export)
 */
export function lookupIndex(
  idx: VaultIndex,
  id: string,
): { offset: number; length: number } {
  const entry = idx[id];
  if (!entry) {
    throw new VaultError("index_miss", `금고 색인에 없는 항목: ${id}`);
  }
  const [offset, length] = entry;
  if (
    !Number.isInteger(offset) ||
    !Number.isInteger(length) ||
    offset < 0 ||
    length <= 0
  ) {
    throw new VaultError("corrupt", `금고 색인 구간이 잘못되었습니다: ${id}`);
  }
  return { offset, length };
}

/**
 * gzip 멤버 1개(바이트 구간)를 풀어 레코드로 만든다. id 가 다르면 throw —
 * 색인이 낡았거나 파일이 바뀐 경우를 잡아낸다.
 * (순수 함수 — 단위 테스트용으로 export)
 */
export function parseVaultMember(
  bytes: Uint8Array,
  expectedId: string,
): VaultRecord {
  let text: string;
  try {
    text = gunzipSync(bytes).toString("utf8");
  } catch (e) {
    throw new VaultError(
      "corrupt",
      `금고 멤버 압축 해제 실패 (${expectedId}): ${(e as Error).message}`,
    );
  }
  let record: unknown;
  try {
    record = JSON.parse(text.trim());
  } catch {
    throw new VaultError(
      "corrupt",
      `금고 레코드 JSON 파싱 실패 (${expectedId})`,
    );
  }
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    throw new VaultError(
      "corrupt",
      `금고 레코드 형식이 아닙니다 (${expectedId})`,
    );
  }
  const r = record as VaultRecord;
  if (r.id !== expectedId) {
    throw new VaultError(
      "id_mismatch",
      `금고 레코드 id 불일치: 기대 ${expectedId}, 실제 ${String(r.id)}`,
    );
  }
  return {
    ...r,
    analysis: r.analysis ?? null,
    analysis_history: Array.isArray(r.analysis_history)
      ? r.analysis_history
      : [],
    related_sources: Array.isArray(r.related_sources) ? r.related_sources : [],
    body: r.body ?? null,
    policy: r.policy ?? null,
  };
}

// ---- Storage HTTP ----------------------------------------------------------

/**
 * 계약 C6 — 세 구현(vault.py·backup 미러·여기) 공통 규칙:
 * apikey 헤더는 항상, Authorization: Bearer 는 키가 'sb_secret_'·'sb_publishable_'
 * 로 시작하면(새 형식 키 = JWT 아님) 보내지 않는다. supabase-js 와 같은 규칙.
 */
function storageHeaders(): Record<string, string> {
  const { supabaseServiceRoleKey } = serverEnv();
  const headers: Record<string, string> = { apikey: supabaseServiceRoleKey };
  const isNewKey =
    supabaseServiceRoleKey.startsWith("sb_secret_") ||
    supabaseServiceRoleKey.startsWith("sb_publishable_");
  if (!isNewKey) headers.Authorization = `Bearer ${supabaseServiceRoleKey}`;
  return headers;
}

function objectUrl(path: string): string {
  const { supabaseUrl } = serverEnv();
  // 배치(vault.py)의 verify가 쓰는 경로와 동일하게 둔다 — 그래야 배치가
  // 확인하는 요청이 화면이 실제로 보내는 요청과 같아진다.
  return `${supabaseUrl.replace(/\/$/, "")}/storage/v1/object/authenticated/${BUCKET}/${path}`;
}

function assertPeriod(period: string) {
  if (!PERIOD_RE.test(period)) {
    throw new VaultError(
      "input",
      `period 키 형식이 잘못되었습니다: ${period} (YYYY-MM-a|b)`,
    );
  }
}

/**
 * Next 는 같은 렌더 안에서 URL·옵션이 같은 GET fetch 를 메모이제이션한다
 * (next/dist/docs/01-app/03-api-reference/04-functions/fetch.md). 재시도(fresh)가
 * 첫 응답을 그대로 돌려받지 않도록 AbortController signal 을 넘겨 빠져나온다 —
 * 문서가 정한 opt-out 방법이고(dedupe-fetch.js), URL 에 쿼리를 덧붙이지 않아
 * Storage 경로가 배치의 verify 요청과 그대로 같다.
 */
function fetchInit(headers: Record<string, string>, fresh: boolean): RequestInit {
  const init: RequestInit = { headers, cache: "no-store" };
  if (fresh) init.signal = new AbortController().signal;
  return init;
}

/** fetch 자체가 던진 것(DNS·연결 끊김 등)은 전부 일시 장애로 본다. */
async function doFetch(url: string, init: RequestInit, what: string): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (e) {
    throw new VaultError(
      "http",
      `${what} 요청 실패: ${e instanceof Error ? e.message : String(e)}`,
    );
  }
}

// idx.json 메모리 캐시 — 프로세스 수명 (설계 §5). Promise 를 넣어 동시 요청이
// 같은 다운로드를 공유하게 하고, 실패하면 지워서 다음 요청이 다시 시도한다.
const indexCache = new Map<string, Promise<VaultIndex>>();

async function fetchIndex(period: string, fresh: boolean): Promise<VaultIndex> {
  const res = await doFetch(
    objectUrl(`items/${period}.idx.json`),
    fetchInit(storageHeaders(), fresh),
    `금고 색인 (${period})`,
  );
  if (res.status === 404) {
    throw new VaultError(
      "missing",
      `금고 색인 파일이 없습니다 (${period}): HTTP 404`,
    );
  }
  if (!res.ok) {
    throw new VaultError(
      "http",
      `금고 색인 내려받기 실패 (${period}): HTTP ${res.status}`,
    );
  }
  let idx: unknown;
  try {
    idx = await res.json();
  } catch {
    throw new VaultError("corrupt", `금고 색인 JSON 파싱 실패 (${period})`);
  }
  if (!idx || typeof idx !== "object" || Array.isArray(idx)) {
    throw new VaultError("corrupt", `금고 색인 형식이 아닙니다 (${period})`);
  }
  return idx as VaultIndex;
}

function loadIndex(period: string, { fresh = false } = {}): Promise<VaultIndex> {
  if (fresh) indexCache.delete(period);
  let p = indexCache.get(period);
  if (!p) {
    p = fetchIndex(period, fresh);
    indexCache.set(period, p);
    p.catch(() => indexCache.delete(period));
  }
  return p;
}

async function fetchMember(
  period: string,
  offset: number,
  length: number,
  fresh: boolean,
): Promise<Uint8Array> {
  const where = `${period} @${offset}+${length}`;
  const res = await doFetch(
    objectUrl(`items/${period}.jsonl.gz`),
    fetchInit(
      { ...storageHeaders(), Range: `bytes=${offset}-${offset + length - 1}` },
      fresh,
    ),
    `금고 구간 (${where})`,
  );
  if (res.status === 404) {
    throw new VaultError(
      "missing",
      `금고 데이터 파일이 없습니다 (${period}): HTTP 404`,
    );
  }
  if (res.status === 416) {
    // 색인이 가리키는 구간이 파일 밖 — 파일이 바뀌었거나 색인이 다른 파일 것
    throw new VaultError(
      "corrupt",
      `금고 구간이 파일 범위를 벗어났습니다 (${where}): HTTP 416`,
    );
  }
  if (res.status !== 206) {
    throw new VaultError(
      "http",
      `금고 구간 요청이 206 이 아닙니다 (${where}): HTTP ${res.status}`,
    );
  }
  const bytes = new Uint8Array(await res.arrayBuffer());
  if (bytes.byteLength !== length) {
    throw new VaultError(
      "corrupt",
      `금고 구간 길이 불일치 (${where}): 실제 ${bytes.byteLength}`,
    );
  }
  return bytes;
}

/**
 * 금고에서 레코드 1건을 읽는다 (설계 §5).
 * idx.json(period 별 캐시) → Range 로 멤버 1개 → gunzip → JSON → id 확인.
 * 색인에 없거나 id 가 어긋나면(만) 캐시를 버리고 한 번 더 시도한다 — period 파일이
 * 다시 내보내져 구간이 바뀐 경우를 흡수한다. HTTP 장애·손상은 재시도하지 않는다
 * (계약 [39]). 그래도 안 되면 throw.
 */
export async function getVaultRecord(
  period: string,
  id: string,
): Promise<VaultRecord> {
  assertPeriod(period);
  const idx = await loadIndex(period);
  try {
    return await readWithIndex(period, id, idx, false);
  } catch (e) {
    if (!isRetryable(e)) throw e;
    const freshIdx = await loadIndex(period, { fresh: true });
    return await readWithIndex(period, id, freshIdx, true);
  }
}

async function readWithIndex(
  period: string,
  id: string,
  idx: VaultIndex,
  fresh: boolean,
): Promise<VaultRecord> {
  const { offset, length } = lookupIndex(idx, id);
  const bytes = await fetchMember(period, offset, length, fresh);
  return parseVaultMember(bytes, id);
}
