/**
 * 배치와 화면이 함께 쓰는 순수 계산 — 여기 있는 규칙은 모두
 * scripts/kiro_batch/ 의 대응 함수를 그대로 옮긴 것이다.
 *
 * 이 파일에는 서버 전용 코드를 두지 않는다. 강제 분석 화면(클라이언트
 * 컴포넌트)과 서버 액션(admin/actions.ts)이 **같은 함수**로 같은 값을
 * 보게 하는 것이 목적이기 때문이다. 한쪽에만 규칙을 복사해 두면
 * 화면은 고를 수 있다고 하고 서버는 거절하는 상태가 다시 생긴다.
 */

// ------------------------------------------------------------
// 쿼터일·시각 (scripts/kiro_batch/quota.py)
// ------------------------------------------------------------
// Gemini는 서머타임과 무관하게 UTC-8 고정으로 하루를 끊는다(연중 KST 17시).
// IANA "America/Los_Angeles"를 쓰면 여름에 UTC-7이 되어 KST 16~17시의 한
// 시간 동안 화면이 배치와 다른 날짜를 세게 된다 (quota.py 2026-08-12 정정).
const QUOTA_OFFSET_MS = -8 * 3_600_000;
const KST_OFFSET_MS = 9 * 3_600_000;

export const DAY_MS = 86_400_000;

/** 쿼터일(UTC-8 기준 날짜) — quota.py 의 quota_date_pt. */
export function quotaDatePt(now: Date = new Date()): string {
  return new Date(now.getTime() + QUOTA_OFFSET_MS).toISOString().slice(0, 10);
}

/** 한국 시각의 시(0~23). */
export function kstHour(now: Date = new Date()): number {
  return new Date(now.getTime() + KST_OFFSET_MS).getUTCHours();
}

// 야간 창 (quota.py 의 NIGHT_START_KST·NIGHT_END_KST)
export const NIGHT_START_KST = 17;
export const NIGHT_END_KST = 6;

export function isNightKst(hour: number): boolean {
  return hour >= NIGHT_START_KST || hour < NIGHT_END_KST;
}

/**
 * 야간(KST 17시~익일 6시)에 적용되는 일일 상한.
 * quota.py 의 nightly_soft_limit — 야간에는 조간 뉴스 분석 몫을 남겨둔다.
 */
export function nightlySoftLimit(
  softLimit: number,
  morningReserve: number,
  hour: number,
): number {
  if (isNightKst(hour)) return Math.max(0, softLimit - morningReserve);
  return softLimit;
}

// ------------------------------------------------------------
// 시간 예산·건수 캡 (freshness.py · analyze.py)
// ------------------------------------------------------------

/**
 * 이번 실행에 주어지는 시간 예산(초) — freshness.py 의 batch_budget_seconds.
 * 수동 실행은 워크플로가 ARTICLE_BATCH_MAX_SECONDS를 비워 보내므로
 * (analyze.yml: 정기 크론 2개에만 값을 준다) 이 시간대별 값을 받는다.
 */
export function batchBudgetSeconds(hour: number, base: number): number {
  if (hour >= 3 && hour < 8) return 540;
  if (hour >= 8 && hour < 14) return 360;
  if (hour >= 14 && hour < 17) return base;
  return 240;
}

// '막차 소진' (analyze.py maybe_enable_last_call_drain + freshness.should_drain).
// 쿼터 리셋(KST 17시)까지 3시간 이내이고 대기가 '이번 배치가 처리할 수 있는
// 양'(runaway_cap_floor) 이상이면 발동한다 — freshness.should_drain과 같다.
// 리셋 3시간 전 = KST 14시이므로 시(hour) 판정과 결과가 같다.
export const DRAIN_WINDOW_START_KST = 14;
export const DRAIN_BUDGET_SECONDS = 540; // analyze.py: ARTICLE_DRAIN_MAX_SECONDS 기본값
export const DRAIN_COUNT_CAP = 999;

export function isDrainWindow(hour: number): boolean {
  return hour >= DRAIN_WINDOW_START_KST && hour < NIGHT_START_KST;
}

/**
 * 건수 캡의 하한 — analyze.py 의 runaway_cap_floor.
 * 캡이 시간 예산보다 먼저 걸리면 배치가 예산을 남긴 채 끝나므로,
 * 정기·기본 실행에서는 캡을 이 값까지 끌어올린다.
 * (운영자가 건수를 지정한 실행에는 적용되지 않는다 — 의도 존중)
 */
export function runawayCapFloor(
  budgetSeconds: number,
  modelCount: number,
  targetRpm: number,
  workers: number,
): number {
  const perSecond = (modelCount * Math.max(0, targetRpm)) / 60;
  const overrun = modelCount * Math.max(1, workers);
  return Math.ceil(Math.max(0, budgetSeconds) * perSecond) + overrun;
}

// ------------------------------------------------------------
// 실측 상수 (2026-09-08 분석 배치 4회)
// ------------------------------------------------------------
/** 기사 1건당 Gemini 호출 수 (재시도 포함). */
export const CALLS_PER_ARTICLE = 1.05;
/**
 * 모델 1개가 기사 1건을 처리하는 데 쓰는 시간(초).
 * 실측은 2모델 병렬에서 1건당 2.7초였다 — 모델 하나뿐이면 그 절반 속도다.
 * (배치는 모델마다 별도 스트림을 돌리고 속도 한도도 모델별로 잡는다)
 */
export const SECONDS_PER_ARTICLE_PER_MODEL = 5.4;

/**
 * 무료 티어 RPD — 모델당 하루 한도. 실측으로 확인했다: 2026-08-11에
 * gemini-flash-lite-latest가 성공 499콜을 찍고 그 뒤 429를 19건 받았다.
 * 그래서 그날 시도 합계(518)는 이 값을 넘는다 — 일별 표의 막대가 100%를
 * 넘는 날은 '한도를 넘긴 게 아니라 넘으려다 거절당한 날'이다.
 */
export const MODEL_RPD_LIMIT = 500;

/** 주어진 시간 예산 안에 처리할 수 있는 기사 수. */
export function articlesInBudget(
  budgetSeconds: number,
  modelCount: number,
): number {
  if (modelCount <= 0) return 0;
  return Math.floor(
    (Math.max(0, budgetSeconds) * modelCount) / SECONDS_PER_ARTICLE_PER_MODEL,
  );
}

/** 기사 n건을 처리하는 데 걸리는 시간(분, 올림·최소 1분). */
export function articleMinutes(articles: number, modelCount: number): number {
  if (articles <= 0 || modelCount <= 0) return 0;
  const seconds = (articles * SECONDS_PER_ARTICLE_PER_MODEL) / modelCount;
  return Math.max(1, Math.round(seconds / 60));
}

// ------------------------------------------------------------
// 설정값 파싱 — app_settings 값 하나를 숫자로 읽는 규칙
// ------------------------------------------------------------
/**
 * 화면·서버 액션이 함께 쓰는 단 하나의 규칙:
 * 값이 없거나(null·undefined·빈 문자열) 숫자가 아니거나 음수면 기본값.
 * **0은 유효한 설정값이다** (예: 브리프 예약 0회 = 예약하지 않음).
 * Number(null)이 0인 탓에 "설정 없음"을 "한도 0"으로 오판하지 않도록
 * 빈 값을 먼저 거른다.
 */
export function settingNumber(value: unknown, fallback: number): number {
  if (value === null || value === undefined || value === "") return fallback;
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

// ------------------------------------------------------------
// 수동 실행 규칙 — 화면 문구와 서버 액션의 검증이 같은 값을 봐야 한다
// ------------------------------------------------------------
/** 모델을 지정하지 않고 배치 기본값(2모델 병렬)으로 돌리는 선택지. */
export const ANALYZE_BOTH = "both";
/**
 * 수동 실행 일일 상한 (2026-08-11). 8/8~8/11 실측에서 analyze가 236회 수동
 * 실행됐고 8/11 하루에만 190분을 썼다 — GitHub Actions 월 한도 2,000분을
 * 이 하나로 넘길 페이스였다.
 */
export const MANUAL_RUN_DAILY_LIMIT = 8;
/** 운영자가 직접 적을 수 있는 건수 범위. 비우면 시간이 되는 만큼 처리한다. */
export const MANUAL_COUNT_MIN = 10;
export const MANUAL_COUNT_MAX = 1000;

// ------------------------------------------------------------
// 이번 실행 예측 — 화면과 서버 액션이 같은 값을 보게 하는 단일 계산
// ------------------------------------------------------------
export type QuotaSettings = {
  dailySoftLimit: number;
  briefDailyReserve: number;
  morningReserveCalls: number;
  batchMaxCount: number;
  batchMaxSeconds: number;
  targetRpm: number;
  streamWorkers: number;
};

export type QuotaAllowance = {
  /** 모델 하나가 오늘 쓸 수 있는 호출 수 (야간 예약분·브리프 예약분 제외 후). */
  perModelCap: number;
  /** 주어진 모델들의 남은 호출 수 합계. */
  callsLeft: number;
  /** 그 호출로 처리할 수 있는 기사 수 — 화면·서버가 함께 쓰는 '남은 여유'. */
  articlesLeft: number;
  /** 야간(조간 몫 보존 구간)인가. */
  night: boolean;
};

/**
 * 오늘 남은 여유 — analyze.py 의 종료 조건과 같은 식이다.
 * (`calls_today >= nightly_soft_limit(...) - brief_daily_reserve` 인 모델은 멈춘다)
 *
 * 배치의 건수 캡은 모델 합산이고 쿼터는 모델별이므로, 남은 호출은 모델별로
 * 잘라 더한 뒤 기사 수로 바꾼다.
 */
export function quotaAllowance(
  usedByModel: number[],
  settings: Pick<
    QuotaSettings,
    "dailySoftLimit" | "briefDailyReserve" | "morningReserveCalls"
  >,
  hour: number,
): QuotaAllowance {
  const effectiveLimit = nightlySoftLimit(
    settings.dailySoftLimit,
    settings.morningReserveCalls,
    hour,
  );
  const perModelCap = Math.max(0, effectiveLimit - settings.briefDailyReserve);
  const callsLeft = usedByModel.reduce(
    (sum, used) => sum + Math.max(0, perModelCap - used),
    0,
  );
  return {
    perModelCap,
    callsLeft,
    articlesLeft: Math.floor(callsLeft / CALLS_PER_ARTICLE),
    night: isNightKst(hour),
  };
}

export type RunPlan = QuotaAllowance & {
  /** 막차 소진이 걸리는가 (건수를 지정해도 걸린다 — 시간 예산만 늘어난다). */
  drain: boolean;
  /** 이번 실행의 시간 예산(초). */
  budgetSeconds: number;
  /** 이번 실행의 건수 캡. */
  countCap: number;
  /** 시간 예산만으로 처리 가능한 기사 수. */
  budgetArticles: number;
  /** 실제로 이번 실행이 멈출 것으로 보는 지점(건). */
  expected: number;
  /** expected를 정한 한계가 무엇인지. */
  limitedBy: "time" | "count" | "quota" | "queue";
};

/**
 * 이번 강제 분석이 실제로 어디에서 멈추는지 — analyze.py main()의 순서를
 * 그대로 따라간다.
 *  1) 시간 예산 = batch_budget_seconds(KST 시, 설정값)
 *  2) 건수 지정이 없으면 캡을 runaway_cap_floor까지 끌어올린다
 *  3) 리셋 3시간 전이고 대기 ≥ runaway_cap_floor면 막차 소진 — 예산이
 *     540초로 늘어난다. 건수를 지정했으면 그 캡은 그대로 두고(999로 덮지
 *     않고) 시간만 늘린다.
 *  4) 실제 종료는 시간·건수·모델별 쿼터·대기 물량 중 먼저 걸리는 것
 */
export function planAnalyzeRun(input: {
  hour: number;
  /** 이번 실행이 쓰는 모델별 오늘 사용 호출 수. */
  usedByModel: number[];
  settings: QuotaSettings;
  pendingCount: number;
  /** 운영자가 지정한 건수. null이면 '시간 되는 만큼'. */
  requestedCount: number | null;
}): RunPlan {
  const { hour, usedByModel, settings, pendingCount, requestedCount } = input;
  const modelCount = usedByModel.length;
  const allowance = quotaAllowance(usedByModel, settings, hour);

  const baseSeconds = batchBudgetSeconds(hour, settings.batchMaxSeconds);
  const capFloor = runawayCapFloor(
    baseSeconds,
    modelCount,
    settings.targetRpm,
    settings.streamWorkers,
  );

  // 막차 판정의 잣대는 언제나 cap_floor다 — 운영자가 고른 숫자를 잣대로 쓰면
  // 큰 값을 적을수록 막차가 안 걸리는 모순이 생긴다 (analyze.py main()이
  // maybe_enable_last_call_drain에 cap_floor를 넘기는 것과 같다).
  const drain = isDrainWindow(hour) && pendingCount >= capFloor;
  // 막차는 시간 예산을 늘린다. 건수를 지정했든 아니든 늘어난다.
  const budgetSeconds = drain ? DRAIN_BUDGET_SECONDS : baseSeconds;

  let countCap: number;
  if (requestedCount === null) {
    // 캡 하한은 건수를 비웠을 때만 올라가고, 막차면 사실상 무제한이 된다.
    countCap = drain
      ? DRAIN_COUNT_CAP
      : Math.max(settings.batchMaxCount, capFloor);
  } else {
    // 운영자가 고른 숫자는 상한으로만 쓰인다 — 막차도 이 값을 덮지 않는다.
    countCap = requestedCount;
  }

  const budgetArticles = articlesInBudget(budgetSeconds, modelCount);
  // 먼저 걸리는 것이 이번 실행을 멈춘다 (analyze.py _process_stream의 종료 조건).
  // 동점이면 앞의 것을 남긴다 — 시간·건수·쿼터·대기 순으로 설명이 자연스럽다.
  const limits: { by: RunPlan["limitedBy"]; n: number }[] = [
    { by: "time", n: budgetArticles },
    { by: "count", n: countCap },
    { by: "quota", n: allowance.articlesLeft },
    { by: "queue", n: Math.max(0, pendingCount) },
  ];
  const tightest = limits.reduce((a, b) => (b.n < a.n ? b : a));

  return {
    ...allowance,
    drain,
    budgetSeconds,
    countCap,
    budgetArticles,
    expected: Math.max(0, tightest.n),
    limitedBy: tightest.by,
  };
}
