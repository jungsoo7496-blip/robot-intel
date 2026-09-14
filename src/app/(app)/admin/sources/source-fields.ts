/**
 * 뉴스 수집원 폼·표가 공유하는 상수·타입.
 *
 * "use server" 파일은 async 함수만 export할 수 있어 상수는 여기에 둔다.
 * adapter_config 키는 수집 배치(scripts/kiro_batch/collect.py, list_page.py)가
 * 읽는 이름과 정확히 같아야 한다:
 *   RSS       → { link_decoder: "google_news" } (구글 뉴스 RSS만)
 *   LIST_PAGE → { item_selector, title_selector?, summary_selector?, base_url? }
 *
 * 네이버·구글 뉴스 '검색어' 수집원은 이 화면이 아니라 /admin/search-keywords가
 * 관리한다 (sources.managed_by='keyword_search' 행). 이 화면은 그 행을 목록에서
 * 빼고, 저장·삭제도 거부한다 — 이름·주소를 다음 수집 때 배치가 다시 계산하므로
 * 여기서 고쳐 봐야 되돌아간다.
 */

/** sources.source_type CHECK 제약과 동일한 목록 (initial_schema). */
export const SOURCE_TYPES = [
  "로봇·산업 전문매체",
  "일반 언론",
  "전문 기술 큐레이션 매체",
  "정부·공공기관",
  "기업 공식 발표",
  "연구기관·대학",
  "정책·사업 공고",
] as const;

export type FetchMethod = "RSS" | "NAVER_API" | "LIST_PAGE" | "PREDEFINED";

export const FETCH_METHOD_LABELS: Record<FetchMethod, string> = {
  RSS: "RSS 피드",
  NAVER_API: "네이버 뉴스 검색",
  LIST_PAGE: "목록 페이지",
  PREDEFINED: "사전 정의 어댑터",
};

/**
 * 화면에서 새로 만들 수 있는 방식 (사용자 요구 1-3).
 * - PREDEFINED: 코드 어댑터가 있어야 해서 제외
 * - NAVER_API: 검색어라서 /admin/search-keywords가 담당 — 여기서는 못 만든다
 */
export const CREATABLE_FETCH_METHODS: readonly FetchMethod[] = ["RSS", "LIST_PAGE"];

/** 기존 14개 분포(10×1, 30×5, 40×8)와 DB 기본값(40)에 맞춘 기본 우선순위. */
export const DEFAULT_PRIORITY = 40;
/** 현재 모든 수집원이 100분 간격. */
export const DEFAULT_FETCH_INTERVAL = 100;
/** 옛 네이버 행의 '조회 건수' 표시 기본값 (검색어 설정은 검색 키워드 화면에 있다). */
export const DEFAULT_NAVER_DISPLAY = 300;

export const REGION_SUGGESTIONS = ["국내", "미국", "유럽", "일본", "중국", "기타"];
export const LANGUAGE_SUGGESTIONS = ["ko", "en", "ja", "zh"];

/** sources 테이블 행 (화면에서 쓰는 컬럼만). */
export type SourceRow = {
  id: string;
  name: string;
  source_type: string;
  country_region: string;
  language: string;
  url: string;
  fetch_method: FetchMethod;
  adapter_config: Record<string, unknown> | null;
  fetch_interval_minutes: number;
  priority: number;
  is_active: boolean;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_error_message: string | null;
  notes: string | null;
  updated_at: string;
  /**
   * 'keyword_search'면 검색 키워드 화면이 소유하는 자동 생성 행이다.
   * 마이그레이션 26 적용 전에는 컬럼이 없어 undefined가 들어온다.
   */
  managed_by?: string | null;
  search_target?: string | null;
  keyword_term?: string | null;
};

/** 검색 키워드 화면이 관리하는 행인가 — 이 화면에서 고칠 수 없다. */
export function isKeywordManaged(
  row: Pick<SourceRow, "managed_by">,
): boolean {
  return row.managed_by === "keyword_search";
}

/** 폼 상태 — 숫자도 문자열로 들고 있다가 서버 액션에서 검증·변환한다. */
export type SourceFormValues = {
  id: string;
  name: string;
  source_type: string;
  fetch_method: FetchMethod;
  country_region: string;
  language: string;
  url: string;
  priority: string;
  fetch_interval_minutes: string;
  notes: string;
  // RSS
  link_decoder: boolean;
  // LIST_PAGE
  item_selector: string;
  title_selector: string;
  summary_selector: string;
  base_url: string;
};

export function defaultFormValues(): SourceFormValues {
  return {
    id: "",
    name: "",
    source_type: "",
    fetch_method: "RSS",
    country_region: "국내",
    language: "ko",
    url: "",
    priority: String(DEFAULT_PRIORITY),
    fetch_interval_minutes: String(DEFAULT_FETCH_INTERVAL),
    notes: "",
    link_decoder: false,
    item_selector: "",
    title_selector: "",
    summary_selector: "",
    base_url: "",
  };
}

function cfgString(cfg: Record<string, unknown>, key: string): string {
  const v = cfg[key];
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

/** DB 행 → 편집 폼 초기값. */
export function formValuesFromRow(row: SourceRow): SourceFormValues {
  const cfg = row.adapter_config ?? {};
  return {
    ...defaultFormValues(),
    id: row.id,
    name: row.name,
    source_type: row.source_type,
    fetch_method: row.fetch_method,
    country_region: row.country_region,
    language: row.language,
    url: row.url,
    priority: String(row.priority),
    fetch_interval_minutes: String(row.fetch_interval_minutes),
    notes: row.notes ?? "",
    link_decoder: cfg.link_decoder === "google_news",
    item_selector: cfgString(cfg, "item_selector"),
    title_selector: cfgString(cfg, "title_selector"),
    summary_selector: cfgString(cfg, "summary_selector"),
    base_url: cfgString(cfg, "base_url"),
  };
}

/**
 * 표에 넣을 짧은 주소 — 사이트 이름(호스트)만 남기고 'www.'는 뗀다.
 * 전체 주소는 표를 가로로 넘치게 하므로 마우스 오버(title)와 편집 화면에서만
 * 보여준다. 주소 형식이 아니면 원문을 그대로 돌려준다(표에서 CSS가 자른다).
 */
export function hostLabel(url: string): string {
  try {
    const host = new URL(url).hostname.replace(/^www\./i, "");
    return host || url;
  } catch {
    return url;
  }
}

/** 표의 '수집 방식' 칸에 넣을 요약. */
export type AdapterSummary = {
  /** 첫 줄 — RSS·목록 페이지는 사이트 이름, 검색어 수집원은 검색어 */
  primary: string;
  /** 둘째 줄 — 선택자·링크 해독 등 부가 설명. 없으면 null */
  secondary: string | null;
  /** 마우스를 올렸을 때 보여줄 전체 주소. 주소가 없는 방식이면 null */
  fullUrl: string | null;
};

/**
 * 표의 '수집 방식' 칸 요약 — 검색어 / 사이트 이름 / 선택자.
 *
 * 전체 주소는 넣지 않는다: 구글 뉴스 RSS 주소는 150자가 넘어 표가 가로로
 * 넘친다(운영자 피드백). 전체 주소는 fullUrl(마우스 오버)과 편집 화면에서 본다.
 *
 * NAVER_API 갈래는 마이그레이션 26 적용 전(관리행 태깅 전)에만 쓰인다.
 * 태깅이 끝나면 그 행들은 이 화면 목록에서 빠진다.
 */
export function describeAdapter(
  row: Pick<SourceRow, "fetch_method" | "url" | "adapter_config">,
): AdapterSummary {
  const cfg = row.adapter_config ?? {};
  switch (row.fetch_method) {
    case "NAVER_API":
      // 주소가 naver-news://query/... 라 사람에게 보여줄 값이 아니다.
      return {
        primary: `검색어 “${cfgString(cfg, "query")}”`,
        secondary: `최신 ${cfgString(cfg, "display") || DEFAULT_NAVER_DISPLAY}건 조회`,
        fullUrl: null,
      };
    case "RSS":
      return {
        primary: hostLabel(row.url),
        secondary:
          cfg.link_decoder === "google_news" ? "구글 뉴스 링크 해독" : null,
        fullUrl: row.url,
      };
    case "LIST_PAGE": {
      const sel = cfgString(cfg, "item_selector");
      return {
        primary: hostLabel(row.url),
        secondary: sel ? `항목 선택자 ${sel}` : null,
        fullUrl: row.url,
      };
    }
    default:
      return {
        primary: hostLabel(row.url),
        secondary: Object.keys(cfg).length ? JSON.stringify(cfg) : null,
        fullUrl: row.url,
      };
  }
}
