import Link from "next/link";

import { createServiceRoleClient } from "@/lib/supabase/server";

import { getRegenerateStatus } from "./actions";
import {
  RegenerateRowButton,
  SpecificWeekForm,
  type WeekOption,
} from "./regenerate-form";

export const dynamic = "force-dynamic";

const BRIEF_STATUS_LABEL: Record<string, string> = {
  PUBLISHED: "발행됨",
  FAILED: "실패",
  DRAFT: "초안",
};

const PERIOD_STATUS_NOTE: Record<string, string> = {
  PUBLISHED: "발행됨",
  FAILED: "실패",
  GENERATING: "생성 중",
  PENDING: "아직 없음",
};

/*
 * 실패 사유를 짧은 한국어로 (2026-09-08, 사용자 요구 6-1 / 피드백 7).
 *
 * 배치(generate_brief.py)는 짧은 한국어만 저장한다 — 예외 원문(모델명·HTTP
 * 코드·응답 본문)은 DB가 아니라 GitHub Actions 실행 로그(stderr)에 남는다.
 * 다만 이전에 저장된 행에는
 * "gemini-flash-lite-latest: 503 UNAVAILABLE. {'error': {...}}; 호출 예산 소진"
 * 같은 원문이 그대로 들어 있다(2026-09-08 확인: 4건 — 503 원문 1건, '호출 예산
 * 소진' 3건). DB는 건드리지 않고 여기서 읽기 쉽게 바꿔 보여 준다.
 * 위에서부터 먼저 맞는 것을 쓰므로 구체적인 규칙을 앞에 둔다.
 */
const FAILURE_PATTERNS: readonly [RegExp, string][] = [
  // 옛 문구 '호출 예산 소진'은 Gemini 한도가 아니라 우리 코드가 건 시도 횟수를
  // 뜻했다 — 운영자가 한도 소진으로 오해해 문구를 바꿨다.
  [/호출 예산 소진|재시도 횟수/, "AI 재시도 횟수 초과"],
  [/429|하루 호출 한도|RESOURCE_EXHAUSTED|rate ?limit/i, "AI 하루 호출 한도 초과"],
  [/50[0234]|UNAVAILABLE|high demand|overload|서버 혼잡/i, "AI 서버 혼잡(일시적)"],
  [/형식 오류|JSON|Validation|파싱/i, "AI 응답 형식 오류"],
  [/timed?\s?out|timeout|시간 초과|DEADLINE/i, "AI 응답 시간 초과"],
];

/**
 * 표시 순서 — generate_brief.REASON_PRIORITY와 같다. 운영자가 먼저 알아야 할
 * 원인이 위다. '재시도 횟수 초과'는 결과이지 원인이 아니라 뒤에 둔다.
 */
const REASON_ORDER: readonly string[] = [
  "AI 하루 호출 한도 초과",
  "AI 서버 혼잡(일시적)",
  "AI 응답 형식 오류",
  "AI 응답 시간 초과",
  "AI 재시도 횟수 초과",
  "AI 생성 실패",
];

/**
 * 저장된 실패 사유 → 화면에 쓸 한 줄.
 * 섹션마다 사유가 다르면 가장 중요한 것 하나만 보여 주고 나머지는 개수로 줄인다
 * (전체 원문은 셀의 툴팁에 그대로 있다).
 */
function shortFailureReason(raw: string | null): string | null {
  if (!raw) return null;
  const labels: string[] = [];
  for (const part of raw.split(";")) {
    const text = part.trim();
    if (!text) continue;
    const hit = FAILURE_PATTERNS.find(([re]) => re.test(text));
    const label = hit ? hit[1] : "AI 생성 실패";
    if (!labels.includes(label)) labels.push(label);
  }
  if (labels.length === 0) return "AI 생성 실패";
  const rank = (l: string) => {
    const i = REASON_ORDER.indexOf(l);
    return i === -1 ? REASON_ORDER.length : i;
  };
  labels.sort((a, b) => rank(a) - rank(b));
  return labels.length > 1 ? `${labels[0]} 외 ${labels.length - 1}건` : labels[0];
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** 지난 8개 완결 주차(월~일)의 목록, 최근 순. KST 기준 오늘이 속한 주는 제외. */
function recentCompletedWeeks(count = 8): { start: string; end: string }[] {
  const todayKst = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
  }).format(new Date());
  const today = new Date(`${todayKst}T00:00:00Z`);
  const weekday = (today.getUTCDay() + 6) % 7; // 월=0
  const thisMonday = new Date(today);
  thisMonday.setUTCDate(today.getUTCDate() - weekday);

  const weeks: { start: string; end: string }[] = [];
  for (let i = 1; i <= count; i++) {
    const start = new Date(thisMonday);
    start.setUTCDate(thisMonday.getUTCDate() - 7 * i);
    const end = new Date(start);
    end.setUTCDate(start.getUTCDate() + 6);
    weeks.push({ start: isoDate(start), end: isoDate(end) });
  }
  return weeks;
}

/** 브리프 관리 (tasks §14.3): 상태 확인 + 주차 지정 재생성 (사용자 요구 5). */
export default async function AdminBriefsPage() {
  const supabase = createServiceRoleClient();
  const [{ data: briefs }, { data: periods }, regen] = await Promise.all([
    supabase
      .from("briefs")
      .select("*, brief_periods(period_start, period_end)")
      .order("generated_at", { ascending: false })
      .limit(40),
    supabase
      .from("brief_periods")
      .select("period_start, status")
      .order("period_start", { ascending: false })
      .limit(60),
    getRegenerateStatus(),
  ]);

  const periodStatus = new Map<string, string>();
  for (const p of periods ?? []) periodStatus.set(p.period_start, p.status);

  const weekOptions: WeekOption[] = recentCompletedWeeks().map((w) => ({
    ...w,
    note: PERIOD_STATUS_NOTE[periodStatus.get(w.start) ?? "PENDING"] ?? "",
  }));

  // 표는 버전 단위 행이므로, 같은 기간의 첫 행(가장 최근 생성분)에만 버튼을 둔다
  const seenPeriods = new Set<string>();

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">브리프 관리</h1>

      {regen.busy && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-200">
          지금 생성 중입니다. 1~3분 뒤 새로고침하세요.
        </div>
      )}

      <div className="rounded-lg border border-black/10 p-4 text-sm dark:border-white/15">
        <h2 className="font-semibold">다시 생성</h2>
        <p className="mb-3 mt-1 text-black/60 dark:text-white/60">
          끝난 주차만 만들 수 있습니다. 다시 만들어도 기존 발행본은 지워지지
          않고 새 버전으로 쌓입니다. 화면에서는 24시간에 {regen.limit}회까지
          (지금까지 {regen.used}회).
        </p>
        <SpecificWeekForm weeks={weekOptions} />
        <p className="mt-2 text-xs text-black/50 dark:text-white/50">
          버튼이 안 되면 GitHub의{" "}
          <a
            href="https://github.com/jungsoo7496-blip/robot-intel/actions/workflows/generate-brief.yml"
            target="_blank"
            rel="noopener noreferrer"
            className="underline"
          >
            generate-brief 워크플로 ↗
          </a>
          에서 Run workflow (week_start = 월요일 날짜).
        </p>
      </div>

      <div className="overflow-x-auto rounded-lg border border-black/10 dark:border-white/15">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="border-b border-black/10 text-left text-black/60 dark:border-white/15 dark:text-white/60">
            <tr>
              <th className="p-3">기간</th>
              <th className="p-3">버전</th>
              <th className="p-3">상태</th>
              <th className="p-3">생성 시각</th>
              <th className="p-3">실패 사유</th>
              <th className="p-3"></th>
            </tr>
          </thead>
          <tbody>
            {(briefs ?? []).length === 0 && (
              <tr>
                <td
                  colSpan={6}
                  className="p-3 text-black/50 dark:text-white/50"
                >
                  아직 생성된 브리프가 없습니다. 위 &lsquo;다시 생성&rsquo;에서
                  주차를 골라 만드세요.
                </td>
              </tr>
            )}
            {(briefs ?? []).map((b) => {
              const periodStart: string | null =
                b.brief_periods?.period_start ?? null;
              const showButton =
                periodStart !== null && !seenPeriods.has(periodStart);
              if (periodStart !== null) seenPeriods.add(periodStart);
              const failure = shortFailureReason(b.failure_reason);
              return (
                <tr
                  key={b.id}
                  className="border-b border-black/5 last:border-0 dark:border-white/10"
                >
                  <td className="p-3">
                    {b.status === "PUBLISHED" ? (
                      <Link href={`/briefs/${b.id}`} className="underline">
                        {b.brief_periods
                          ? `${b.brief_periods.period_start} ~ ${b.brief_periods.period_end}`
                          : b.title}
                      </Link>
                    ) : b.brief_periods ? (
                      `${b.brief_periods.period_start} ~ ${b.brief_periods.period_end}`
                    ) : (
                      b.title
                    )}
                  </td>
                  <td className="p-3">
                    v{b.version}
                    {b.is_current && " (현재)"}
                  </td>
                  <td className="p-3">
                    <span
                      title={b.status}
                      className={
                        b.status === "PUBLISHED"
                          ? "text-green-700 dark:text-green-400"
                          : b.status === "FAILED"
                            ? "text-red-600"
                            : "text-black/60 dark:text-white/60"
                      }
                    >
                      {BRIEF_STATUS_LABEL[b.status] ?? b.status}
                    </span>
                    {b.status === "PUBLISHED" && failure && (
                      <span
                        className="ml-1 text-xs text-amber-700 dark:text-amber-400"
                        title="세 섹션 중 일부만 실패했고, 나머지로 발행했습니다."
                      >
                        일부 섹션만 실패
                      </span>
                    )}
                  </td>
                  <td className="p-3">
                    {b.generated_at
                      ? new Date(b.generated_at).toLocaleString("ko-KR", {
                          timeZone: "Asia/Seoul",
                        })
                      : "—"}
                  </td>
                  {/* 저장된 값은 title 속성에 그대로 두고(옛 행은 원문)
                      화면에는 짧은 한국어만 보여 준다 */}
                  <td
                    className="p-3 text-xs text-black/50 dark:text-white/50"
                    title={b.failure_reason ?? undefined}
                  >
                    {failure ?? "—"}
                  </td>
                  <td className="p-3">
                    {showButton && periodStart && (
                      <RegenerateRowButton weekStart={periodStart} />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
