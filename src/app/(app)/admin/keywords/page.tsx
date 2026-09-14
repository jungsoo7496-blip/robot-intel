import Link from "next/link";

import { requireOperator } from "@/lib/auth";
import { createServiceRoleClient } from "@/lib/supabase/server";

import {
  FilterTester,
  MinBodyLengthForm,
  RuleChip,
  RuleForm,
  type KindOption,
} from "./rule-form";

export const dynamic = "force-dynamic";

type Rule = {
  id: string;
  rule_set: string;
  kind: string;
  term: string;
  is_regex: boolean;
  enabled: boolean;
  note: string | null;
};

// anchor: 이 kind가 비면 배치가 그 rule_set 전체를 내장 기본값으로 되돌린다
// (local_filter.from_db는 robot, classify.configure는 core, collect_rnd.configure는 keyword를 본다).
type KindInfo = KindOption & { help: string; anchor?: boolean };

type Section = {
  ruleSet: "news_filter" | "report_tier" | "rnd_keywords";
  title: string;
  intro: string;
  kinds: KindInfo[];
};

// 각 kind가 무슨 뜻인지 — 운영자는 비개발자이므로 쉬운 말로.
// 배치 쪽 정의: scripts/kiro_batch/keyword_rules.py RULE_SETS
const SECTIONS: Section[] = [
  {
    ruleSet: "news_filter",
    title: "뉴스 필터",
    intro:
      "뉴스를 AI에 보내기 전에 서버가 먼저 거릅니다. 제외되면 AI에 가지 않고, 애매하면 " +
      "보류로 두어 AI가 나중에 판단합니다. 실제 판정 순서는 이 묶음 맨 아래 '판정 미리보기'로 확인하세요.",
    kinds: [
      {
        value: "robot",
        label: "로봇 단어 (robot)",
        help: "제목·본문에 이 단어가 하나도 없으면 로봇과 무관한 기사로 보고 제외합니다 (대소문자 무시).",
        anchor: true,
      },
      {
        value: "strong",
        label: "확실한 단어 (strong)",
        help: "제목에 이 단어가 있으면 확실한 통과로 봅니다.",
      },
      {
        value: "exclude",
        label: "즉시 제외 (exclude)",
        help: "광고·채용·주가 등 — 제목이나 본문에 걸리면 로봇 단어가 있어도 바로 제외합니다 (정규식).",
        regexDefault: true,
      },
      {
        value: "event_only",
        label: "행사 안내 (event_only)",
        help: "제목에 이 패턴이 있으면 단순 행사 안내로 보고 보류합니다 (정규식).",
        regexDefault: true,
      },
    ],
  },
  {
    ruleSet: "report_tier",
    title: "보고서 계층",
    intro:
      "정책·동향 보고서를 제목·초록의 단어로 핵심/인접으로 나눕니다. 둘 다 없으면 수집하지 않습니다.",
    kinds: [
      {
        value: "core",
        label: "핵심 (core)",
        help: "로봇이 주제인 보고서 — 정상 우선순위로 분석합니다.",
        anchor: true,
      },
      {
        value: "tool",
        label: "인접 (tool)",
        help: "드론·AI·스마트공장처럼 로봇이 도구로 쓰이는 분야 — 수집은 하되 우선순위를 낮춥니다.",
      },
    ],
  },
  {
    ruleSet: "rnd_keywords",
    title: "R&D 공고",
    intro:
      "NTIS 국가R&D 공고 중 로봇 관련 공고를 골라 표시합니다 (AI 분석 없이 표시만). " +
      "놓치지 않도록 넓게 잡습니다.",
    kinds: [
      {
        value: "keyword",
        label: "단어 (keyword)",
        help: "이 단어가 들어 있으면 로봇 관련 공고로 표시합니다 (부분 일치, 대소문자 무시).",
        anchor: true,
      },
      {
        value: "pattern",
        label: "정규식 (pattern)",
        help: "'AI' 같은 짧은 영문 약어는 chain·maintain에도 걸리므로, 앞뒤에 영문·숫자가 없을 때만 맞도록 씁니다. 예: (?<![a-z0-9])ai(?![a-z0-9])",
        regexDefault: true,
      },
    ],
  },
];

const MIN_BODY_SETTING_KEY = "filter_min_body_length";
const DEFAULT_MIN_BODY_LENGTH = 200;

function toInt(value: unknown, fallback: number): number {
  const n =
    typeof value === "number"
      ? value
      : typeof value === "string"
        ? Number(value)
        : Number.NaN;
  return Number.isFinite(n) ? Math.trunc(n) : fallback;
}

/**
 * 수집 규칙 (관리 기능 — 사용자 요구 3). 뉴스 필터·보고서 계층·R&D 공고.
 *
 * 2026-09-08 이름 변경(피드백 6): 화면 이름 '키워드 규칙' → '수집 규칙'.
 * 운영자가 '검색 키워드'(무엇을 찾을지)와 헷갈려 해서 바꿨다. 라우트
 * (/admin/keywords)와 표 이름(keyword_rules)은 그대로 둔다 — 링크·배치가
 * 여러 곳에서 참조하므로 이름만 바꾼다.
 */
export default async function AdminKeywordsPage() {
  // 레이아웃의 검사만 믿지 않는다 — 레이아웃은 화면 이동 때 다시 실행되지 않을 수
  // 있어(Next 문서 authentication.md '레이아웃과 인증 검사'), 아래에서 RLS를 우회하는
  // 서비스 키로 규칙을 읽기 전에 여기서 다시 확인한다.
  await requireOperator();
  const supabase = createServiceRoleClient();
  const [rulesRes, settingRes] = await Promise.all([
    supabase
      .from("keyword_rules")
      .select("id, rule_set, kind, term, is_regex, enabled, note")
      .order("created_at")
      .order("term"),
    supabase
      .from("app_settings")
      .select("value")
      .eq("key", MIN_BODY_SETTING_KEY)
      .maybeSingle(),
  ]);

  const rules = (rulesRes.data ?? []) as Rule[];
  const byKey = new Map<string, Rule[]>();
  for (const r of rules) {
    const key = `${r.rule_set}/${r.kind}`;
    const list = byKey.get(key);
    if (list) list.push(r);
    else byKey.set(key, [r]);
  }
  const minBodyLength = toInt(settingRes.data?.value, DEFAULT_MIN_BODY_LENGTH);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">수집 규칙</h1>
        <Link
          href="/admin"
          className="rounded border border-black/15 px-2 py-1 text-sm hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
        >
          ← 운영 현황
        </Link>
      </div>
      <p className="text-sm text-black/60 dark:text-white/60">
        <Link href="/admin/search-keywords" className="underline">
          검색 키워드
        </Link>
        는 <strong>무엇을 찾을지</strong>, 수집 규칙은{" "}
        <strong>찾아온 것 중 무엇을 남길지</strong>를 정합니다.
      </p>
      <p className="text-sm text-black/50 dark:text-white/50">
        규칙 추가·삭제는 다음 수집 배치부터 반영됩니다 (뉴스 하루 3회, 보고서·R&D 공고 하루 1회).
      </p>
      {rulesRes.error && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
          규칙 목록을 불러오지 못했습니다 — 잠시 뒤 새로고침해 보세요. 수집은 평소대로
          계속됩니다. (원인: {rulesRes.error.message})
        </div>
      )}

      {SECTIONS.map((section) => (
        <section
          key={section.ruleSet}
          className="space-y-4 rounded-lg border border-black/10 p-4 dark:border-white/15"
        >
          <div>
            <h2 className="text-lg font-semibold">
              {section.title}{" "}
              <span className="font-mono text-xs font-normal text-black/40 dark:text-white/40">
                {section.ruleSet}
              </span>
            </h2>
            <p className="mt-1 text-sm text-black/60 dark:text-white/60">{section.intro}</p>
          </div>

          {section.kinds.map((kind) => {
            const list = byKey.get(`${section.ruleSet}/${kind.value}`) ?? [];
            const enabledCount = list.filter((r) => r.enabled).length;
            return (
              <div key={kind.value} className="space-y-1.5">
                <h3 className="text-sm font-semibold">
                  {kind.label}{" "}
                  <span className="text-xs font-normal text-black/50 dark:text-white/50">
                    {list.length}개
                    {/* 화면에서 끌 수는 없지만 옛 데이터가 남아 있을 수 있어, 있을 때만 알린다 */}
                    {list.length !== enabledCount &&
                      ` (배치가 안 쓰는 옛 규칙 ${list.length - enabledCount}개 포함)`}
                  </span>
                </h3>
                <p className="text-xs text-black/60 dark:text-white/60">{kind.help}</p>
                <div className="flex flex-wrap gap-1.5">
                  {list.map((r) => (
                    <RuleChip
                      key={r.id}
                      id={r.id}
                      term={r.term}
                      isRegex={r.is_regex}
                      enabled={r.enabled}
                      note={r.note}
                    />
                  ))}
                </div>
                {/* 배치는 enabled=true 규칙만 읽는다. 폴백은 kind별이 아니라 '기준 종류가
                    비면 rule_set 전체'라서 문구를 그에 맞춘다 (local_filter.from_db 등). */}
                {enabledCount === 0 && (
                  <p className="text-xs text-amber-700 dark:text-amber-300">
                    {kind.anchor
                      ? `쓰는 규칙 없음 — 이 종류가 비면 '${section.title}' 전체가 코드에 내장된 기본 목록으로 돌아갑니다.`
                      : "쓰는 규칙 없음 — 배치가 이 단계를 건너뜁니다 (기본값으로 대체되지 않음)."}
                  </p>
                )}
              </div>
            );
          })}

          <div className="border-t border-black/10 pt-3 dark:border-white/15">
            <h3 className="mb-1 text-sm font-semibold">규칙 추가</h3>
            <RuleForm ruleSet={section.ruleSet} kinds={section.kinds} />
          </div>

          {section.ruleSet === "news_filter" && (
            <>
              <div className="border-t border-black/10 pt-3 dark:border-white/15">
                <MinBodyLengthForm value={minBodyLength} />
              </div>
              <div className="border-t border-black/10 pt-3 dark:border-white/15">
                <h3 className="text-sm font-semibold">판정 미리보기</h3>
                <p className="mb-2 text-xs text-black/60 dark:text-white/60">
                  제목·본문을 넣으면 지금 규칙으로 어떻게 판정되는지 보여줍니다 (참고용 —
                  실제 배치와 드물게 다를 수 있습니다).
                </p>
                <FilterTester />
              </div>
            </>
          )}
        </section>
      ))}
    </div>
  );
}
