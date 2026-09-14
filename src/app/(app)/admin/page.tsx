import Link from "next/link";

import { formatDate } from "@/components/item-card";

import { getDashboardStats } from "./stats";

export const dynamic = "force-dynamic";

function Stat({
  label,
  value,
  warn = false,
  sub,
}: {
  label: string;
  value: React.ReactNode;
  warn?: boolean;
  sub?: string;
}) {
  return (
    <div
      className={`rounded-lg border p-3 ${
        warn
          ? "border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950/40"
          : "border-black/10 dark:border-white/15"
      }`}
    >
      <div className="text-xs text-black/50 dark:text-white/50">{label}</div>
      <div className="mt-0.5 text-lg font-semibold">{value}</div>
      {sub && (
        <div className="text-xs text-black/40 dark:text-white/40">{sub}</div>
      )}
    </div>
  );
}

function formatDateTime(value: string | null) {
  if (!value) return "기록 없음";
  return new Date(value).toLocaleString("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Seoul",
  });
}

// 2026-09-08 정리(사용자 요구 4): '수동 URL 등록'은 등록 0건이라 화면째 지웠다.
// '검색 키워드'는 네이버·구글 뉴스와 보고서 수집원이 함께 쓰는 검색어 화면이다.
//
// 2026-09-08 메뉴 정리(피드백 6): 하는 일이 가까운 것끼리 묶고, 묶음 이름을 앞에
// 붙였다 — 수집(무엇을 어디서 찾아 무엇을 남길지) / 게시(독자에게 보이는 것) /
// 점검(용량·사용량). '키워드 규칙'은 '검색 키워드'와 헷갈린다는 지적을 받아
// '수집 규칙'으로 바꿨다(라우트는 그대로).
// 라벨은 각 화면의 제목과 같게 둔다 — 눌러서 들어간 화면 제목이 달라 보이면
// 오히려 헷갈린다.
const ADMIN_MENU = [
  {
    group: "수집",
    items: [
      { href: "/admin/search-keywords", label: "검색 키워드" },
      { href: "/admin/sources", label: "뉴스 수집원" },
      { href: "/admin/report-sources", label: "보고서 수집원" },
      { href: "/admin/keywords", label: "수집 규칙" },
    ],
  },
  {
    group: "게시",
    items: [
      { href: "/admin/error-reports", label: "콘텐츠·신고 관리" },
      { href: "/admin/briefs", label: "브리프 관리" },
    ],
  },
  {
    group: "점검",
    items: [
      { href: "/admin/data", label: "데이터 관리" },
      { href: "/admin/usage", label: "Gemini·Actions 사용 현황" },
    ],
  },
] as const;

/** 운영 현황 (tasks §15.2) — 월 1회 점검용 한 화면 요약. */
export default async function AdminHomePage() {
  const s = await getDashboardStats();
  const budgetPct = Math.round((s.monthMinutes / s.budgetMinutes) * 100);

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-bold">운영 현황</h1>
        <nav className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          {ADMIN_MENU.map((g) => (
            <div key={g.group} className="flex flex-wrap items-center gap-1.5">
              <span className="text-xs text-black/45 dark:text-white/45">{g.group}</span>
              {g.items.map((m) => (
                <Link
                  key={m.href}
                  href={m.href}
                  className="rounded border border-black/15 px-2 py-1 hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
                >
                  {m.label}
                </Link>
              ))}
            </div>
          ))}
        </nav>
      </div>

      {s.staleWarning && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
          ⚠️ 24시간 이상 수집 배치 성공 기록이 없습니다. GitHub Actions의
          collect-and-analyze 워크플로를 확인하거나 수동 실행하세요.
        </div>
      )}

      <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <Stat label="오늘 수집" value={`${s.collectedToday}건`} />
        <Stat label="오늘 게시" value={`${s.publishedToday}건`} />
        <Stat label="관련성 제외 누적" value={`${s.excludedTotal}건`} />
        <Stat label="중복 클러스터" value={`${s.clusterCount}개`} />
        <Stat
          label="AI 분석 성공·실패"
          value={`${s.jobCounts.DONE ?? 0} / ${s.jobCounts.FAILED ?? 0}`}
        />
        <Stat
          label="분석 대기"
          value={`${s.pendingCount}건`}
          sub={
            s.oldestPendingMinutes != null
              ? `최장 대기 ${Math.floor(s.oldestPendingMinutes / 60)}시간 ${s.oldestPendingMinutes % 60}분`
              : undefined
          }
          warn={(s.oldestPendingMinutes ?? 0) > 12 * 60}
        />
        <Stat
          label="오늘 Gemini 호출 (PT 기준)"
          value={`${s.geminiOk}회`}
          sub={`429: ${s.gemini429}회`}
          warn={s.gemini429 > 0}
        />
        <Stat label="30분 잠금 복구 누적" value={`${s.staleRecoveredCount}건`} />
        <Stat
          label="마지막 수집 성공"
          value={formatDateTime(s.lastCollectAt)}
          warn={s.staleWarning}
        />
        <Stat label="마지막 분석 성공" value={formatDateTime(s.lastAnalyzeAt)} />
        <Stat
          label="이번 달 Actions 사용 (청구 추정)"
          value={`${s.monthMinutes}분`}
          sub={`무료 한도 ${s.budgetMinutes}분의 ${budgetPct}% · 준비시간·올림 포함, CI 제외라 실제는 이보다 많을 수 있음`}
          warn={s.monthMinutes >= s.warnMinutes}
        />
        <Stat
          label="운영자가 숨긴 콘텐츠"
          value={`${s.operatorHiddenCount}건`}
          sub={
            s.openReportCount > 0
              ? `독자 오류 신고 ${s.openReportCount}건 처리 필요`
              : "독자 오류 신고 없음"
          }
          warn={s.openReportCount > 0}
        />
      </section>

      <section className="grid gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-black/10 p-4 text-sm dark:border-white/15">
          <h2 className="font-semibold">최근 브리프</h2>
          {s.latestBrief ? (
            <p className="mt-1">
              {s.latestBrief.title}
              <br />
              <span
                className={
                  s.latestBrief.status === "PUBLISHED"
                    ? "text-green-700 dark:text-green-400"
                    : "text-red-600"
                }
              >
                {s.latestBrief.status}
              </span>{" "}
              · {formatDate(s.latestBrief.published_at ?? s.latestBrief.generated_at)}
            </p>
          ) : (
            <p className="mt-1 text-black/50 dark:text-white/50">발행 이력 없음</p>
          )}
        </div>
        <div className="rounded-lg border border-black/10 p-4 text-sm dark:border-white/15">
          <h2 className="font-semibold">최근 백업·keepalive</h2>
          {s.latestBackup ? (
            <p className="mt-1">
              {formatDateTime(s.latestBackup.started_at)} ·{" "}
              {s.latestBackup.status}
              {s.latestBackup.keepalive_performed && " · keepalive 수행"}
              {s.latestBackup.keepalive_status &&
                ` (${s.latestBackup.keepalive_status})`}
            </p>
          ) : (
            <p className="mt-1 text-black/50 dark:text-white/50">
              백업 실행 이력 없음 — 주 1회 backup 워크플로가 기록합니다
            </p>
          )}
        </div>
      </section>

      {s.failingSources.length > 0 && (
        <section className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm dark:border-amber-800 dark:bg-amber-950/40">
          <h2 className="font-semibold">장기 실패 의심 수집원</h2>
          <ul className="mt-1 list-disc pl-5">
            {s.failingSources.map((src) => (
              <li key={src.name}>
                {src.name} — 마지막 실패 {formatDateTime(src.last_failure_at)}
                {src.last_error_message && `: ${src.last_error_message.slice(0, 80)}`}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
