"use server";

import { revalidatePath } from "next/cache";

import { requireOperator } from "@/lib/auth";
import { createServiceRoleClient } from "@/lib/supabase/server";

/**
 * 수집 규칙 관리 서버 액션 (2026-09-08, 관리 기능 — 사용자 요구 3).
 *
 * 화면 이름만 '수집 규칙'이다(피드백 6 — '검색 키워드'와 헷갈림). 표 이름
 * keyword_rules, 라우트 /admin/keywords, 이벤트 KEYWORD_RULE_*는 그대로 둔다.
 *
 * keyword_rules 테이블을 고친다. 배치(scripts/kiro_batch)는 시작할 때 이 표를
 * 읽으므로 변경은 다음 수집 배치부터 반영된다. 비밀값은 다루지 않는다.
 * 모든 조치는 operation_events(KEYWORD_RULE_*)에 남긴다.
 *
 * 2026-09-08 정리(사용자 요구 3-1): 화면에서는 '추가·삭제'만 한다. 켜기/끄기
 * 토글은 없앴다 — 운영자가 쓴 적이 없고(전 221행 enabled=true) '꺼진 규칙'이
 * 화면에만 남아 실제 동작과 어긋나 보이기 때문이다. DB의 enabled 컬럼은
 * 그대로 두고(배치가 이 값을 읽는다) 새 규칙은 항상 enabled=true로 넣는다.
 */

export type RuleSet = "news_filter" | "report_tier" | "rnd_keywords";
export type ActionState = { ok: boolean; message: string } | null;
export type FilterVerdict = "PASS" | "LOW_PRIORITY" | "EXCLUDE";
export type FilterTestState =
  | {
      ok: true;
      status: FilterVerdict;
      reason: string;
      steps: string[];
      summary: string;
    }
  | { ok: false; message: string }
  | null;

// rule_set → 허용 kind. scripts/kiro_batch/keyword_rules.py RULE_SETS와 같아야 한다.
const RULE_KINDS: Record<RuleSet, readonly string[]> = {
  news_filter: ["robot", "strong", "exclude", "event_only"],
  report_tier: ["core", "tool"],
  rnd_keywords: ["keyword", "pattern"],
};

const PAGE_PATH = "/admin/keywords";
const MIN_BODY_SETTING_KEY = "filter_min_body_length";
const DEFAULT_MIN_BODY_LENGTH = 200;
const TERM_MAX_LENGTH = 200;
const NOTE_MAX_LENGTH = 300;

function isRuleSet(value: string): value is RuleSet {
  return Object.prototype.hasOwnProperty.call(RULE_KINDS, value);
}

type Supabase = ReturnType<typeof createServiceRoleClient>;

/** 운영 조치 이력 (tasks §14.2). 실패해도 조치 자체는 되돌리지 않고 로그만 남긴다. */
async function logEvent(
  supabase: Supabase,
  eventType: string,
  targetId: string | null,
  actorId: string,
  reason: string,
  targetTable = "keyword_rules",
) {
  const { error } = await supabase.from("operation_events").insert({
    event_type: eventType,
    target_table: targetTable,
    target_id: targetId,
    actor_id: actorId,
    reason: reason.slice(0, 500),
  });
  if (error) {
    console.error(`[admin/keywords] operation_events 기록 실패 (${eventType}):`, error.message);
  }
}

// 파이썬 표준 re가 아는 문자 이스케이프. 그 밖의 \글자(\p \h \c \k \e …)는 JS만
// 통과시키고, 배치가 표준 re로 컴파일하면 그 규칙이 통째로 빠진다(keyword_rules.compile_terms).
// 화면에는 '활성'으로 보여 운영자가 알 수 없으므로 저장 단계에서 막는다.
const PY_KNOWN_ESCAPES = new Set("AbBdDsSwWZafnrtvxuUN".split(""));

function isQuantifierStart(c: string | undefined): boolean {
  return c === "*" || c === "+" || c === "{";
}

/**
 * 반복 안에 반복이 있는가 — (a+)+ · (\w+\s?)* · (.*a){20} 류.
 *
 * 이런 정규식은 일치하지 않는 긴 본문에서 되짚기가 지수적으로 늘어나 수집 배치를
 * 세운다(파이썬 re에는 시간 제한이 없다). 배치 쪽 판단과 어긋나면 안 되므로
 * scripts/kiro_batch/keyword_rules.py has_nested_quantifier와 같은 규칙으로 본다.
 */
function hasNestedQuantifier(pattern: string): boolean {
  const stack: boolean[] = []; // 바깥 그룹들이 지금까지 본 반복 여부
  let inClass = false; // [...] 안의 +*{ 는 반복이 아니라 글자다
  let quantSeen: boolean = false;
  for (let i = 0; i < pattern.length; i += 1) {
    const c = pattern[i];
    if (c === "\\") {
      i += 1; // 이스케이프된 글자는 통째로 건너뛴다
    } else if (inClass) {
      if (c === "]") inClass = false;
    } else if (c === "[") {
      inClass = true;
    } else if (c === "(") {
      stack.push(quantSeen);
      quantSeen = false;
    } else if (c === ")") {
      const inner: boolean = quantSeen; // 명시 — 좁혀진 리터럴 타입이 순환 추론을 만든다
      const parent = stack.pop() ?? false;
      const repeated = isQuantifierStart(pattern[i + 1]);
      if (inner && repeated) return true;
      quantSeen = parent || inner || repeated;
    } else if (isQuantifierStart(c)) {
      quantSeen = true;
    }
  }
  return false;
}

/**
 * 정규식 검사 — 문제가 있으면 운영자에게 보일 이유, 없으면 null.
 *
 * 배치는 파이썬으로 판정하므로 JS에서만 되는 문법을 저장하면 그 규칙이 조용히
 * 무시되고(화면에는 '활성'으로 보인다), 되짚기가 폭발하는 정규식은 배치를 멈춘다.
 * 둘 다 저장 전에 막는다.
 */
function regexProblem(term: string): string | null {
  try {
    new RegExp(term, "i");
  } catch (e) {
    return `정규식 문법 오류: ${e instanceof Error ? e.message : String(e)}`;
  }
  if (hasNestedQuantifier(term)) {
    return "반복 안에 반복이 있는 정규식(예: (a+)+, (로봇\\s*)+)은 수집 배치를 멈출 수 있어 저장할 수 없습니다.";
  }
  if (/\(\?<(?![=!])/.test(term)) {
    return "이름 붙은 그룹은 (?P<이름>…) 형식으로 써 주세요 — (?<이름>…)은 배치에서 규칙이 통째로 무시될 수 있습니다.";
  }
  if (/\\k</.test(term)) {
    return "\\k<이름> 역참조는 (?P=이름) 으로 써 주세요 — 배치에서 규칙이 통째로 무시될 수 있습니다.";
  }
  for (const m of term.matchAll(/(?<!\\)(?:\\\\)*\\([A-Za-z])/g)) {
    if (!PY_KNOWN_ESCAPES.has(m[1])) {
      return `배치에서 쓸 수 있다고 보장되지 않는 이스케이프 \\${m[1]} 입니다 — 규칙이 무시될 수 있어 저장하지 않습니다.`;
    }
  }
  return null;
}

function parseIntSetting(value: unknown, fallback: number): number {
  const n =
    typeof value === "number"
      ? value
      : typeof value === "string"
        ? Number(value)
        : Number.NaN;
  return Number.isFinite(n) ? Math.trunc(n) : fallback;
}

/** 규칙 추가 (useActionState — { ok, message }). */
export async function addKeywordRule(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const profile = await requireOperator();
  const ruleSet = String(formData.get("rule_set") ?? "");
  const kind = String(formData.get("kind") ?? "");
  const term = String(formData.get("term") ?? "").trim();
  const isRegex = formData.get("is_regex") === "on";
  const note = String(formData.get("note") ?? "").trim().slice(0, NOTE_MAX_LENGTH);

  if (!isRuleSet(ruleSet) || !RULE_KINDS[ruleSet].includes(kind)) {
    return { ok: false, message: "규칙 종류가 올바르지 않습니다." };
  }
  if (!term) return { ok: false, message: "용어를 입력해 주세요." };
  if (term.length > TERM_MAX_LENGTH) {
    return { ok: false, message: `용어는 ${TERM_MAX_LENGTH}자 이내로 입력해 주세요.` };
  }
  if (isRegex) {
    const problem = regexProblem(term);
    if (problem) return { ok: false, message: problem };
  }

  const supabase = createServiceRoleClient();
  const { data, error } = await supabase
    .from("keyword_rules")
    .insert({
      rule_set: ruleSet,
      kind,
      term,
      is_regex: isRegex,
      // 화면에 끄기/켜기가 없으므로 새 규칙은 항상 '쓰는 규칙'으로 넣는다.
      enabled: true,
      note: note || null,
    })
    .select("id")
    .single();
  if (error || !data) {
    if (error?.code === "23505") {
      return { ok: false, message: `이미 등록된 용어입니다: ${term}` };
    }
    return {
      ok: false,
      message: `추가에 실패했습니다: ${error?.message ?? "알 수 없는 오류"}`,
    };
  }

  await logEvent(
    supabase,
    "KEYWORD_RULE_CREATE",
    data.id,
    profile.id,
    `${ruleSet}/${kind}: ${term}${isRegex ? " (정규식)" : ""}`,
  );
  revalidatePath(PAGE_PATH);
  return {
    ok: true,
    message: `추가했습니다: ${term} — 다음 수집 배치부터 적용됩니다.`,
  };
}

/** 규칙 삭제 (되돌릴 수 없음 — 화면에서 확인을 받는다). */
export async function deleteKeywordRule(formData: FormData) {
  const profile = await requireOperator();
  const id = String(formData.get("id") ?? "");
  if (!id) return;

  const supabase = createServiceRoleClient();
  const { data, error } = await supabase
    .from("keyword_rules")
    .delete()
    .eq("id", id)
    .select("rule_set, kind, term")
    .maybeSingle();
  if (error) {
    console.error("[admin/keywords] 삭제 실패:", error.message);
  } else if (data) {
    await logEvent(
      supabase,
      "KEYWORD_RULE_DELETE",
      id,
      profile.id,
      `${data.rule_set}/${data.kind}: ${data.term}`,
    );
  }
  revalidatePath(PAGE_PATH);
}

/** 뉴스 필터 본문 최소 길이 (app_settings 'filter_min_body_length'). */
export async function updateMinBodyLength(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const profile = await requireOperator();
  const raw = String(formData.get("value") ?? "").trim();
  if (!/^\d{1,6}$/.test(raw)) {
    return { ok: false, message: "0 이상의 정수(글자 수)를 입력해 주세요." };
  }
  const value = Number(raw);

  const supabase = createServiceRoleClient();
  const { error } = await supabase.from("app_settings").upsert(
    {
      key: MIN_BODY_SETTING_KEY,
      value,
      description:
        "뉴스 로컬 필터: 본문이 이 글자 수보다 짧으면 보류(LOW_PRIORITY)로 AI에 넘긴다. /admin/keywords에서 조정",
      updated_at: new Date().toISOString(),
    },
    { onConflict: "key" },
  );
  if (error) {
    return { ok: false, message: `저장에 실패했습니다: ${error.message}` };
  }
  await logEvent(
    supabase,
    "KEYWORD_RULE_SETTING",
    null,
    profile.id,
    `${MIN_BODY_SETTING_KEY}=${value}`,
    "app_settings",
  );
  revalidatePath(PAGE_PATH);
  return {
    ok: true,
    message: `본문 최소 길이를 ${value}자로 저장했습니다 — 다음 수집 배치부터 적용됩니다.`,
  };
}

// ------------------------------------------------------------
// 간단 테스터 — scripts/kiro_batch/local_filter.py LocalFilter.evaluate를
// TS로 옮긴 것. 순서를 바꾸면 파이썬 쪽도 함께 바꿀 것:
//   exclude → 키워드 없음 → event_only → 본문 길이 → 제목 키워드 → 본문 3회
// ------------------------------------------------------------

type Matcher = { term: string; find: (textLower: string) => string | null };

// 파이썬 str 패턴에서 \w·\b·\d는 유니코드 기준이라 한글도 단어 문자지만, JS는
// u 플래그를 붙여도 ASCII다. 그대로 두면 'AD\b' 같은 규칙이 미리보기와 배치에서
// 다른 답을 낸다 — 유니코드 문자 클래스로 바꿔 "u" 플래그로 컴파일한다.
const UNICODE_WORD = "[\\p{L}\\p{N}_]";

function toPythonLike(src: string): string {
  return src.replace(
    /(?<!\\)((?:\\\\)*)\\([bBwWdD])/g,
    (_m: string, head: string, c: string) => {
      switch (c) {
        case "b":
          return `${head}(?:(?<=${UNICODE_WORD})(?!${UNICODE_WORD})|(?<!${UNICODE_WORD})(?=${UNICODE_WORD}))`;
        case "B":
          return `${head}(?:(?<=${UNICODE_WORD})(?=${UNICODE_WORD})|(?<!${UNICODE_WORD})(?!${UNICODE_WORD}))`;
        case "w":
          return `${head}${UNICODE_WORD}`;
        case "W":
          return `${head}[^\\p{L}\\p{N}_]`;
        case "d":
          return `${head}\\p{Nd}`;
        default:
          return `${head}[^\\p{Nd}]`;
      }
    },
  );
}

function compileMatcher(term: string, isRegex: boolean): Matcher | null {
  const trimmed = term.trim();
  if (!trimmed) return null;
  if (isRegex) {
    // 배치가 건너뛰는 규칙(문법 오류·중첩 반복·파이썬에 없는 문법)은 여기서도 빼야
    // 미리보기가 실제 판정과 같아진다. 중첩 반복은 미리보기를 돌리는 서버 함수까지
    // 붙잡아 두므로 아예 컴파일하지 않는다.
    if (regexProblem(trimmed)) return null;
    let re: RegExp;
    try {
      re = new RegExp(toPythonLike(trimmed), "iu");
    } catch {
      // 유니코드 모드에서 못 쓰는 표기가 섞인 경우 — 문법은 위에서 확인했다
      re = new RegExp(trimmed, "i");
    }
    return {
      term: trimmed,
      find: (text) => {
        const m = re.exec(text);
        return m ? m[0] : null;
      },
    };
  }
  const needle = trimmed.toLowerCase();
  return { term: trimmed, find: (text) => (text.includes(needle) ? needle : null) };
}

type NewsRules = {
  robot: Matcher[];
  strong: Matcher[];
  exclude: Matcher[];
  event_only: Matcher[];
};

function evaluateNewsFilter(
  rules: NewsRules,
  minBodyLength: number,
  title: string,
  body: string,
): { status: FilterVerdict; reason: string; steps: string[] } {
  const titleLower = title.toLowerCase();
  const bodyLower = body.toLowerCase();
  const combined = `${titleLower}\n${bodyLower}`;
  const steps: string[] = [];

  // 1) 명백한 광고·채용·주가·행사 패턴
  for (const m of rules.exclude) {
    if (m.find(combined)) {
      steps.push(`① 즉시 제외 패턴 일치: ${m.term}`);
      return { status: "EXCLUDE", reason: `제외 패턴 일치: ${m.term}`, steps };
    }
  }
  steps.push("① 즉시 제외 패턴: 없음");

  const titleHits = rules.robot.filter((m) => m.find(titleLower)).map((m) => m.term);
  const bodyHits = rules.robot.filter((m) => m.find(bodyLower)).map((m) => m.term);
  const strongInTitle = rules.strong.some((m) => m.find(titleLower));
  steps.push(
    `② 로봇 단어: 제목 ${titleHits.length}개${titleHits.length ? ` (${titleHits.slice(0, 5).join(", ")})` : ""} · 본문 ${bodyHits.length}개`,
  );

  // 2) 로봇 키워드가 전혀 없으면 명백한 비관련
  if (titleHits.length === 0 && bodyHits.length === 0) {
    return { status: "EXCLUDE", reason: "로봇 관련 키워드 없음", steps };
  }

  // 3) 단순 행사 안내
  for (const m of rules.event_only) {
    if (m.find(titleLower)) {
      steps.push(`③ 행사 안내 패턴 일치(제목): ${m.term}`);
      return { status: "LOW_PRIORITY", reason: `단순 행사 안내 추정: ${m.term}`, steps };
    }
  }
  steps.push("③ 행사 안내 패턴: 없음");

  // 4) 본문 길이
  steps.push(`④ 본문 길이: ${body.length}자 (최소 ${minBodyLength}자)`);
  if (body.length < minBodyLength) {
    return {
      status: "LOW_PRIORITY",
      reason: `본문 길이 부족(${body.length} < ${minBodyLength})`,
      steps,
    };
  }

  // 5) 제목에 핵심 키워드
  if (strongInTitle || titleHits.length >= 1) {
    steps.push(`⑤ 제목에 로봇 단어 있음${strongInTitle ? " (확실한 단어 포함)" : ""}`);
    return { status: "PASS", reason: "제목에 로봇 키워드 존재", steps };
  }
  steps.push("⑤ 제목에 로봇 단어 없음");

  // 6) 본문에만 언급
  steps.push(`⑥ 본문 로봇 단어 ${bodyHits.length}종 (3종 이상이면 통과)`);
  if (bodyHits.length >= 3) {
    return { status: "PASS", reason: `본문 로봇 키워드 ${bodyHits.length}회`, steps };
  }
  return {
    status: "LOW_PRIORITY",
    reason: `본문 로봇 키워드 ${bodyHits.length}회 — 관련성 불확실`,
    steps,
  };
}

/** 뉴스 필터 판정 미리보기 — 현재 활성 규칙으로 제목·본문을 판정한다. */
export async function testNewsFilter(
  _prev: FilterTestState,
  formData: FormData,
): Promise<FilterTestState> {
  await requireOperator();
  const title = String(formData.get("title") ?? "").trim().slice(0, 500);
  const body = String(formData.get("body") ?? "").trim().slice(0, 20000);
  if (!title && !body) {
    return { ok: false, message: "제목이나 본문을 입력해 주세요." };
  }

  const supabase = createServiceRoleClient();
  const [rulesRes, settingRes] = await Promise.all([
    supabase
      .from("keyword_rules")
      .select("kind, term, is_regex")
      .eq("rule_set", "news_filter")
      .eq("enabled", true)
      .order("created_at")
      .order("term"),
    supabase
      .from("app_settings")
      .select("value")
      .eq("key", MIN_BODY_SETTING_KEY)
      .maybeSingle(),
  ]);
  if (rulesRes.error) {
    return { ok: false, message: `규칙을 읽지 못했습니다: ${rulesRes.error.message}` };
  }

  const rules: NewsRules = { robot: [], strong: [], exclude: [], event_only: [] };
  for (const row of rulesRes.data ?? []) {
    const kind = String(row.kind) as keyof NewsRules;
    if (!(kind in rules)) continue;
    const m = compileMatcher(String(row.term), Boolean(row.is_regex));
    if (m) rules[kind].push(m);
  }
  if (rules.robot.length === 0) {
    return {
      ok: false,
      message:
        "'로봇 단어(robot)' 규칙이 하나도 없습니다. 이 상태에서 배치는 코드에 내장된 기본 목록으로 동작하므로 여기서는 판정할 수 없습니다.",
    };
  }
  const minBodyLength = parseIntSetting(settingRes.data?.value, DEFAULT_MIN_BODY_LENGTH);

  const result = evaluateNewsFilter(rules, minBodyLength, title, body);
  return {
    ok: true,
    ...result,
    summary:
      `쓰는 규칙: robot ${rules.robot.length} · strong ${rules.strong.length} · ` +
      `exclude ${rules.exclude.length} · event_only ${rules.event_only.length} · ` +
      `본문 최소 ${minBodyLength}자`,
  };
}
