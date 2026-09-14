/**
 * 검색 키워드 화면(/admin/search-keywords)이 쓰는 상수·타입·순수 함수.
 *
 * "use server" 파일은 async 함수만 export할 수 있어 상수는 여기에 둔다.
 * 표·컬럼 이름은 마이그레이션 20260908000026_search_keywords.sql과 같아야 한다:
 *   search_keywords(term, alt_terms, scope, sort_order, note)
 *   news_search_targets(target_key, is_active, priority, fetch_interval_minutes, display, …)
 *
 * 보고서 수집원 화면(C)은 이 파일에서 import만 하고 고치지 않는다.
 */

// ------------------------------------------------------------
// 검색어 (search_keywords)
// ------------------------------------------------------------

/** search_keywords.scope CHECK 제약과 같은 값. '쓰는 곳'을 뜻한다. */
export type KeywordScope = "ALL" | "NEWS" | "REPORTS";

export const SCOPE_OPTIONS: readonly {
  value: KeywordScope;
  label: string;
  help: string;
}[] = [
  {
    value: "ALL",
    label: "뉴스와 보고서 모두",
    // 보고서 수집원 수는 여기에 적지 않는다 — 검색어를 쓰는 수집원(prism 제외)
    // 개수는 화면 맨 위에서 loadReportSourceCount가 실제 값으로 계산해 띄운다.
    help: "네이버·구글 뉴스와 보고서 수집원 모두에서 찾습니다.",
  },
  {
    value: "NEWS",
    label: "뉴스만",
    help: "네이버·구글 뉴스에서만 찾습니다. 보고서에는 쓰지 않습니다.",
  },
  {
    value: "REPORTS",
    label: "보고서만",
    help: "국회도서관·NKIS 등 보고서 수집원에서만 찾습니다. 뉴스 유입은 늘지 않습니다.",
  },
];

export const SCOPE_LABELS: Record<KeywordScope, string> = {
  ALL: "뉴스와 보고서 모두",
  NEWS: "뉴스만",
  REPORTS: "보고서만",
};

export function isKeywordScope(value: string): value is KeywordScope {
  return value === "ALL" || value === "NEWS" || value === "REPORTS";
}

/** 이 검색어를 뉴스(네이버·구글)에서도 쓰는가. */
export function scopeUsesNews(scope: KeywordScope): boolean {
  return scope === "ALL" || scope === "NEWS";
}

/** 이 검색어를 보고서 수집원에서도 쓰는가. */
export function scopeUsesReports(scope: KeywordScope): boolean {
  return scope === "ALL" || scope === "REPORTS";
}

/** search_keywords 행 (화면에서 쓰는 컬럼만). */
export type SearchKeywordRow = {
  id: string;
  term: string;
  /** 같은 뜻 다른 표기 — 구글 검색에서만 OR로 함께 찾는다. */
  alt_terms: string[];
  scope: KeywordScope;
  sort_order: number;
  note: string | null;
};

/** search_keywords.term CHECK(length(term) <= 60)와 같은 값. */
export const TERM_MAX_LENGTH = 60;
/** 같은 뜻 다른 표기 한 개의 길이 상한 — term과 같은 기준으로 맞춘다. */
export const ALT_TERM_MAX_LENGTH = 60;
/** 같은 뜻 다른 표기 개수 상한 (구글 질의가 지나치게 길어지지 않게). */
export const MAX_ALT_TERMS = 5;
export const NOTE_MAX_LENGTH = 200;

/**
 * 검색어 개수 상한. 검색어 1개가 늘면 뉴스 수집원 2개(네이버·구글)와
 * 보고서 조합 7개가 함께 늘어 수집·분석 부하가 그만큼 커진다.
 */
export const MAX_KEYWORDS = 20;
/** 이 개수를 넘으면 화면에 경고 배너를 띄운다. */
export const KEYWORD_COUNT_WARN = 10;

/**
 * "1, 2, 3" 형태의 입력을 같은 뜻 다른 표기 목록으로.
 * 쉼표(,)와 가운뎃점(·) 모두 구분자로 받는다 — 운영자가 둘 다 쓴다.
 * 대소문자만 다른 중복은 하나로 합친다.
 */
export function normalizeAltTerms(raw: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const piece of raw.split(/[,·]/)) {
    const term = piece.trim();
    if (!term) continue;
    const key = term.toLocaleLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(term);
  }
  return out;
}

// ------------------------------------------------------------
// 구글 뉴스 RSS 주소 만들기 (요구 1-2)
// ------------------------------------------------------------

/**
 * 파이썬 urllib.parse.quote(value, safe="")와 같은 결과를 낸다.
 *
 * encodeURIComponent는 ! ' ( ) * 를 그대로 두지만 파이썬 quote는 인코딩한다.
 * 배치(scripts/kiro_batch/search_keywords.py)가 만드는 주소와 글자까지 같아야
 * 운영자가 이 화면에서 본 주소와 실제 수집 주소가 어긋나지 않는다.
 */
export function pyQuote(value: string): string {
  return encodeURIComponent(value).replace(
    /[!'()*]/g,
    (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`,
  );
}

/**
 * 구글에 넣을 질의문.
 * 같은 뜻 다른 표기가 없으면 검색어 그대로, 있으면 큰따옴표로 묶어 OR로 잇는다.
 * 예: 피지컬 AI + {Physical AI, Embodied AI}
 *     → "피지컬 AI" OR "Physical AI" OR "Embodied AI"
 */
export function googleQuery(term: string, altTerms: readonly string[]): string {
  const base = term.trim();
  const alts = altTerms.map((t) => t.trim()).filter(Boolean);
  if (alts.length === 0) return base;
  return [base, ...alts].map((t) => `"${t}"`).join(" OR ");
}

/** 구글 뉴스 RSS 주소의 고정 꼬리 — 한국어·한국 지역 결과. */
export const GOOGLE_RSS_SUFFIX = "&hl=ko&gl=KR&ceid=KR:ko";

/**
 * 검색어로 만들어지는 구글 뉴스 RSS 주소 (요구 1-2).
 *
 * ※ 이 함수는 화면 미리보기 전용이다. 실제 수집에 쓰는 주소를 sources 표에
 *    만드는 것은 배치(scripts/kiro_batch/search_keywords.py)뿐이며, 두 구현이
 *    같은 문자열을 내도록 파이썬 쪽에 현재 4개 주소 재현 테스트가 있다.
 */
export function googleNewsRssUrl(
  term: string,
  altTerms: readonly string[],
): string {
  return `https://news.google.com/rss/search?q=${pyQuote(
    googleQuery(term, altTerms),
  )}${GOOGLE_RSS_SUFFIX}`;
}

// ------------------------------------------------------------
// 검색 대상 (news_search_targets)
// ------------------------------------------------------------

/** news_search_targets.target_key CHECK 제약과 같은 값. */
export type NewsTargetKey = "naver" | "google";

export type NewsSearchTargetRow = {
  target_key: NewsTargetKey;
  name: string;
  is_active: boolean;
  priority: number;
  fetch_interval_minutes: number;
  /** 네이버 전용 — 한 번에 조회할 최신 기사 수. 구글은 이 값을 쓰지 않는다. */
  display: number;
  source_type: string;
  country_region: string;
  language: string;
};

export function isNewsTargetKey(value: string): value is NewsTargetKey {
  return value === "naver" || value === "google";
}

/** 대상별 화면 설명 — 실제 동작과 정확히 일치해야 한다. */
export const TARGET_INFO: Record<
  NewsTargetKey,
  { title: string; what: string; usesDisplay: boolean }
> = {
  naver: {
    title: "네이버 뉴스 검색",
    what: "네이버 뉴스 검색 API로 검색어마다 최신 기사를 가져옵니다. 국내 기사가 가장 많이 들어오는 경로입니다.",
    usesDisplay: true,
  },
  google: {
    title: "구글 뉴스 RSS",
    what: "구글 뉴스 검색 결과를 RSS로 가져옵니다. 주소는 검색어에서 자동으로 만들어집니다.",
    usesDisplay: false,
  },
};

// ------------------------------------------------------------
// DB 오류 판정
// ------------------------------------------------------------

/**
 * 표·컬럼이 아직 없을 때(마이그레이션 26 적용 전)의 오류인지.
 *
 * PostgREST는 스키마 캐시에 없는 표를 PGRST205, 없는 컬럼을 PGRST204로
 * 돌려주고, 직접 SQL 오류가 새면 42P01(표 없음)·42703(컬럼 없음)이 온다.
 */
export function isMissingTableError(
  error: { code?: string; message: string } | null,
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

/** news_search_targets CHECK 제약과 같은 범위. */
export const DISPLAY_MIN = 100;
export const DISPLAY_MAX = 300;
export const PRIORITY_MIN = 1;
export const PRIORITY_MAX = 100;
export const INTERVAL_MIN = 10;
export const INTERVAL_MAX = 1440;

// ------------------------------------------------------------
// 마이그레이션 적용 전 대비 (관리행 태깅이 아직 없을 때)
// ------------------------------------------------------------

/** sources 행에서 되짚은 '이 행이 담당하는 검색어'. */
export type SourceKeyword = { term: string; target: NewsTargetKey };

/**
 * sources 행이 어느 검색어로 모은 것인지 추정한다.
 *
 * 마이그레이션 26이 적용되면 sources.keyword_term에 값이 들어오므로 그대로 쓰고,
 * 적용 전에는 기존 행의 모양(네이버 adapter_config.query · 구글 URL의 q)에서
 * 되짚는다. 유입량 통계를 보여주기 위한 추정일 뿐 저장에는 쓰지 않는다.
 */
export function guessKeywordOfSource(row: {
  fetch_method: string;
  url: string;
  adapter_config: Record<string, unknown> | null;
  keyword_term?: string | null;
}): SourceKeyword | null {
  if (row.keyword_term) {
    return {
      term: row.keyword_term,
      target: row.fetch_method === "NAVER_API" ? "naver" : "google",
    };
  }
  if (row.fetch_method === "NAVER_API") {
    const q = row.adapter_config?.query;
    return typeof q === "string" && q.trim()
      ? { term: q.trim(), target: "naver" }
      : null;
  }
  if (row.fetch_method !== "RSS" || !row.url.includes("news.google.com")) {
    return null;
  }
  let q: string | null;
  try {
    q = new URL(row.url).searchParams.get("q");
  } catch {
    return null;
  }
  if (!q) return null;
  // 복합 질의("피지컬 AI" OR "Physical AI" …)면 첫 따옴표 안의 말이 대표 검색어다
  const quoted = q.match(/^"([^"]+)"/);
  const term = (quoted ? quoted[1] : q).trim();
  return term ? { term, target: "google" } : null;
}
