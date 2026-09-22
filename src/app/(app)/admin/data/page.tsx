import Link from "next/link";

import { requireOperator } from "@/lib/auth";
import { createServiceRoleClient } from "@/lib/supabase/server";

import {
  CleanupActions,
  RetentionForm,
  type CleanupPreset,
  type RetentionField,
} from "./retention-form";

export const dynamic = "force-dynamic";

// Supabase 무료 플랜 DB 한도 — 넘으면 읽기 전용이 된다
const DB_LIMIT_MB = 500;
// 실측 2026-09-07: 하루 +8 MB. app_settings 'db_growth_mb_per_day'가 있으면 그 값
const DEFAULT_GROWTH_MB_PER_DAY = 8;
const MB = 1024 * 1024;

type TableStat = {
  name: string;
  total_bytes: number;
  table_bytes: number;
  index_bytes: number;
  toast_bytes: number;
  rows: number;
};

/** admin_db_stats() (0022) 반환 — service_role 전용 RPC라 서버 컴포넌트에서만 부른다. */
type DbStats = {
  measured_at: string;
  database_bytes: number;
  tables: TableStat[] | null;
  raw_text_bytes: number;
  clean_text_bytes: number;
  raw_response_bytes: number;
  extract_pending: number;
  extract_pending_over_3d: number;
  analysis_pending: number;
  analysis_pending_over_3d: number;
  exclude_with_body: number;
  raw_items_total: number;
  published_total: number;
  analyses_total: number;
};

const RETENTION_FIELDS: RetentionField[] = [
  {
    key: "extract_expire_days",
    label: "본문 추출 대기 만료",
    help:
      "발행(없으면 수집) 후 이 일수를 넘긴 추출 대기는 EXPIRED로 정리합니다. " +
      "지난 뉴스에 본문 내려받기·Gemini 호출을 쓰지 않기 위한 것입니다.",
    kind: "days",
  },
  {
    key: "analysis_expire_days",
    label: "분석 대기 만료",
    help:
      "원문 발행 후 이 일수를 넘긴 분석 대기는 CANCELLED로 정리합니다. " +
      "운영자가 요청한 재분석과 브리프, 최근 이 일수 안에 다시 큐에 오른 작업" +
      "(대표 출처 교체·재분석 백필·재시도)은 건드리지 않습니다.",
    kind: "days",
  },
  {
    key: "nonrep_body_retention_days",
    label: "비대표 구성원 본문 보존",
    help:
      "같은 사건으로 묶인 기사 중 대표가 아닌 기사의 본문을 며칠 뒤 비울지. " +
      "AI 분석은 대표 본문만 씁니다. 2차 병합 창(7일)보다 짧게 둘 수 없습니다.",
    kind: "days",
  },
  {
    key: "unpublished_retention_days",
    label: "게시 안 된 클러스터 부속 보존",
    help:
      "AI가 로봇 뉴스가 아니라고 판정했거나 다른 사건에 병합돼 카드가 없는 클러스터의 " +
      "분석 결과와 대표 본문을 며칠 뒤 비울지. 금고는 게시된 기사만 담으므로 여기서 비웁니다. " +
      "제목·링크·수집 기록은 남습니다. 0이면 비우지 않습니다.",
    kind: "days",
  },
  {
    key: "raw_text_retention_days",
    label: "raw_text 보존",
    help:
      "수집 후 이 일수가 지난 raw_text를 비웁니다. 지금은 raw_text를 새로 저장하지 " +
      "않으므로(clean_text와 같은 값의 복제였음) 남은 옛 데이터에만 해당합니다.",
    kind: "days",
  },
  {
    key: "keep_exclude_body",
    label: "제외(EXCLUDE) 판정 본문",
    help:
      "로봇과 무관하다고 걸러진 기사의 본문입니다. 읽는 곳이 없습니다. 끄면 새로 " +
      "저장하지 않고, 남아 있는 것도 매일 정리에서 비웁니다.",
    kind: "bool",
  },
  {
    key: "keep_raw_response",
    label: "Gemini 응답 원문",
    help:
      "분석 결과의 원본 JSON 텍스트입니다. 화면은 파싱된 필드만 씁니다. 끄면 정상(PASS) " +
      "분석의 원문을 저장하지 않습니다. 검증 경고·실패(WARN·FAIL)는 항상 보존합니다.",
    kind: "bool",
  },
];

function fmtMb(bytes: number | null | undefined, digits = 1) {
  return `${((bytes ?? 0) / MB).toFixed(digits)} MB`;
}

function fmtInt(n: number | null | undefined) {
  return (n ?? 0).toLocaleString("ko-KR");
}

function formatDateTime(value: string | null | undefined) {
  if (!value) return "—";
  return new Date(value).toLocaleString("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Seoul",
  });
}

function Stat({
  label,
  value,
  sub,
  warn = false,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  warn?: boolean;
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
      {sub && <div className="text-xs text-black/40 dark:text-white/40">{sub}</div>}
    </div>
  );
}

/** 데이터(DB 용량) 관리 — 용량 현황·보존 정책·정리 실행 (사용자 요구 6번, 2026-09-08). */
export default async function AdminDataPage() {
  // 레이아웃도 검사하지만 이 페이지는 service role로 DB 전체 지표를 읽으므로
  // 페이지 자체에서도 한 번 더 막는다 (레이아웃 변경에 딸려 뚫리지 않게)
  await requireOperator();
  const supabase = createServiceRoleClient();
  const settingKeys = [
    ...RETENTION_FIELDS.map((f) => f.key),
    "db_growth_mb_per_day",
  ];

  const [statsRes, settingsRes, runsRes] = await Promise.all([
    supabase.rpc("admin_db_stats"),
    supabase.from("app_settings").select("key, value").in("key", settingKeys),
    supabase
      .from("workflow_usage")
      .select("id, started_at, completed_at, duration_seconds, status, note")
      .eq("workflow_name", "cleanup")
      .order("started_at", { ascending: false })
      .limit(5),
  ]);

  const stats = (statsRes.data ?? null) as DbStats | null;
  const statsError = statsRes.error?.message ?? null;

  const settings = new Map<string, unknown>(
    (settingsRes.data ?? []).map((r) => [r.key as string, r.value]),
  );
  const retentionValues: Record<string, number | boolean> = {};
  for (const f of RETENTION_FIELDS) {
    const v = settings.get(f.key);
    retentionValues[f.key] =
      f.kind === "bool" ? v === true || v === "true" : Number(v ?? 0) || 0;
  }
  const growthPerDay =
    Number(settings.get("db_growth_mb_per_day")) || DEFAULT_GROWTH_MB_PER_DAY;

  // 아래 지표(admin_db_stats)는 3일 고정인데 배치는 보존 정책 값(0이면 안 함)을 따른다.
  // 정책을 3일이 아닌 값으로 바꾼 운영자가 "건수가 안 줄어든다 = 정리가 고장났다"고
  // 오해하지 않도록, 카드 문구와 빨간 경고를 정책 값에서 도출한다.
  const extractDays = Number(retentionValues.extract_expire_days) || 0;
  const analysisDays = Number(retentionValues.analysis_expire_days) || 0;
  const expireNote = (days: number, actor: string, verb: string) =>
    days <= 0
      ? `자동 ${verb} 꺼짐(0일) — 아래 '오래된 대기 정리'를 누를 때만 3일 기준으로 ${verb}`
      : days === 3
        ? `${actor}가 ${verb} 처리`
        : `${actor}는 ${days}일 초과분만 ${verb} 처리 (앞 건수는 3일 기준 지표)`;

  const dbMb = stats ? stats.database_bytes / MB : 0;
  const dbPct = Math.round((dbMb / DB_LIMIT_MB) * 100);
  const remainingMb = Math.max(0, DB_LIMIT_MB - dbMb);
  const daysLeft = Math.floor(remainingMb / growthPerDay);
  // 측정 시각(RPC measured_at)을 기준으로 계산 — 렌더 중 현재 시각을 읽지 않는다
  const measuredAt = stats ? new Date(stats.measured_at).getTime() : 0;
  const limitDate = new Date(measuredAt + daysLeft * 24 * 60 * 60 * 1000);
  const limitDateLabel = limitDate.toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "Asia/Seoul",
  });

  const cleanupPresets: CleanupPreset[] = [
    {
      preset: "daily",
      title: "지금 일일 정리 실행",
      description:
        "매일 03:47에 도는 정리를 지금 한 번 돌립니다. 위 보존 정책대로 EXCLUDE·비대표 " +
        "본문 비우기, 오래된 분석 대기 취소, 90일 지난 만료 행 삭제를 합니다.",
      estimate: stats
        ? `EXCLUDE 본문 ${fmtInt(stats.exclude_with_body)}건 등 (보존 정책이 켜진 항목만)`
        : "지표를 읽지 못해 알 수 없음",
      primary: true,
    },
    {
      preset: "purge_columns",
      title: "일회성: 복제 컬럼 비우기",
      description:
        "raw_text 전량(clean_text와 같은 값의 복제, 읽는 코드 없음)과 정상(PASS) 분석의 " +
        "Gemini 응답 원문을 비웁니다. 2,000행씩 나눠 처리합니다.",
      estimate: stats
        ? `raw_text ${fmtMb(stats.raw_text_bytes)} + 응답 원문 최대 ${fmtMb(stats.raw_response_bytes)} — ` +
          "실제 용량 감소는 VACUUM FULL 후 반영"
        : "지표를 읽지 못해 알 수 없음",
    },
    {
      preset: "purge_backlog",
      title: "일회성: 오래된 대기 정리",
      description:
        "발행이 오래된 추출 대기를 EXPIRED로, 분석 대기를 CANCELLED로 바꿉니다. 기준 일수는 " +
        "위 보존 정책(0이면 3일). 용량보다는 Gemini 호출·배치 시간을 아끼는 작업입니다.",
      estimate: stats
        ? `추출 대기 ${fmtInt(stats.extract_pending_over_3d)}건 → EXPIRED, ` +
          `분석 대기 ${fmtInt(stats.analysis_pending_over_3d)}건 → CANCELLED (3일 기준 지표 — ` +
          `분석 대기는 다시 큐에 오른 지도 그만큼 지난 것만 취소되므로 실제로는 더 적습니다)`
        : "지표를 읽지 못해 알 수 없음",
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold">데이터 관리</h1>
        <Link
          href="/admin"
          className="rounded border border-black/15 px-2 py-1 text-sm hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
        >
          운영 현황으로
        </Link>
      </div>
      <p className="text-sm text-black/60 dark:text-white/60">
        Supabase 무료 플랜의 DB 한도는 {DB_LIMIT_MB} MB이며, 넘으면 사이트가 읽기 전용이
        됩니다. 여기서 현재 용량을 확인하고, 얼마나 오래 보관할지(보존 정책)를 정하고, 정리
        배치를 바로 실행할 수 있습니다. 게시된 글·브리프·분석 결과는 어떤 정리에서도
        지우지 않습니다.
      </p>

      {statsError && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
          용량 지표를 읽지 못했습니다: {statsError} — DB에 admin_db_stats() 함수(마이그레이션
          0022)가 적용됐는지 확인하세요.
        </div>
      )}

      {stats && (
        <>
          {dbPct >= 80 && (
            <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
              ⚠️ DB 사용량이 한도의 {dbPct}%입니다. 아래 &ldquo;복제 컬럼 비우기&rdquo;를
              실행하고 사무실 PC에서 VACUUM FULL을 돌리세요.
            </div>
          )}

          <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            <Stat
              label={`DB 용량 / ${DB_LIMIT_MB} MB`}
              value={`${dbMb.toFixed(1)} MB`}
              sub={`${dbPct}% 사용 · ${formatDateTime(stats.measured_at)} 측정`}
              warn={dbPct >= 80}
            />
            <Stat
              label="한도 도달 예상일"
              value={daysLeft > 3650 ? "10년 이상" : `${limitDateLabel} (${daysLeft}일 뒤)`}
              sub={`하루 +${growthPerDay} MB 가정 (app_settings db_growth_mb_per_day, 없으면 ${DEFAULT_GROWTH_MB_PER_DAY})`}
              warn={daysLeft <= 30}
            />
            <Stat
              label="raw_text 잔량 (복제 컬럼)"
              value={fmtMb(stats.raw_text_bytes)}
              sub="clean_text와 같은 값 · 읽는 코드 없음 · 새로 저장 안 함"
              warn={stats.raw_text_bytes > 5 * MB}
            />
            <Stat
              label="Gemini 응답 원문 잔량"
              value={fmtMb(stats.raw_response_bytes)}
              sub="analyses.raw_response · 파싱된 필드만 화면이 씀"
              warn={stats.raw_response_bytes > 5 * MB}
            />
            <Stat
              label="clean_text 전체"
              value={fmtMb(stats.clean_text_bytes)}
              sub={`원문 ${fmtInt(stats.raw_items_total)}건 중 본문이 있는 것`}
            />
            <Stat
              label="EXCLUDE 판정인데 본문 남음"
              value={`${fmtInt(stats.exclude_with_body)}건`}
              sub="보존 정책이 꺼져 있으면 매일 정리에서 비움"
              warn={stats.exclude_with_body > 0 && retentionValues.keep_exclude_body !== true}
            />
            <Stat
              label="본문 추출 대기"
              value={`${fmtInt(stats.extract_pending)}건`}
              sub={`3일 초과 ${fmtInt(stats.extract_pending_over_3d)}건 — ${expireNote(
                extractDays,
                "다음 수집 배치",
                "만료",
              )}`}
              warn={stats.extract_pending_over_3d > 0 && extractDays > 0 && extractDays <= 3}
            />
            <Stat
              label="분석 대기"
              value={`${fmtInt(stats.analysis_pending)}건`}
              sub={`3일 초과 ${fmtInt(stats.analysis_pending_over_3d)}건 — ${expireNote(
                analysisDays,
                "매일 정리",
                "취소",
              )}`}
              warn={stats.analysis_pending_over_3d > 0 && analysisDays > 0 && analysisDays <= 3}
            />
          </section>

          <section>
            <h2 className="font-semibold">큰 테이블 순서</h2>
            <p className="mt-1 text-sm text-black/60 dark:text-white/60">
              본문 같은 긴 글은 TOAST(별도 저장소)에 들어갑니다. 정리로 비운 공간은 VACUUM FULL
              전까지 이 수치에 남아 있습니다.
            </p>
            <div className="mt-2 overflow-x-auto rounded-lg border border-black/10 dark:border-white/15">
              <table className="w-full min-w-[640px] text-sm">
                <thead className="border-b border-black/10 text-left text-black/60 dark:border-white/15 dark:text-white/60">
                  <tr>
                    <th className="p-2">테이블</th>
                    <th className="p-2 text-right">전체</th>
                    <th className="p-2 text-right">본체</th>
                    <th className="p-2 text-right">긴 글(TOAST)</th>
                    <th className="p-2 text-right">인덱스</th>
                    <th className="p-2 text-right">행 수(추정)</th>
                  </tr>
                </thead>
                <tbody>
                  {(stats.tables ?? []).map((t) => (
                    <tr
                      key={t.name}
                      className="border-b border-black/5 last:border-0 dark:border-white/10"
                    >
                      <td className="p-2 font-mono">{t.name}</td>
                      <td className="p-2 text-right">{fmtMb(t.total_bytes)}</td>
                      <td className="p-2 text-right">{fmtMb(t.table_bytes)}</td>
                      <td className="p-2 text-right">{fmtMb(t.toast_bytes)}</td>
                      <td className="p-2 text-right">{fmtMb(t.index_bytes)}</td>
                      {/* reltuples는 ANALYZE 전이면 -1 */}
                      <td className="p-2 text-right">{t.rows < 0 ? "—" : fmtInt(t.rows)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      <section className="rounded-xl border border-black/10 bg-white p-4 dark:border-white/12 dark:bg-white/[0.03]">
        <h2 className="font-semibold">보존 정책</h2>
        <p className="mt-1 mb-3 text-sm text-black/60 dark:text-white/60">
          매일 03:47 정리 배치와 수집·분석 배치가 이 값을 읽습니다. 일수는 0이면 그 정리를 하지
          않습니다. 저장하면 다음 배치부터 적용되고 변경 이력이 남습니다.
        </p>
        <RetentionForm fields={RETENTION_FIELDS} values={retentionValues} />
      </section>

      <section className="space-y-3">
        <div>
          <h2 className="font-semibold">정리 실행</h2>
          <p className="mt-1 text-sm text-black/60 dark:text-white/60">
            GitHub Actions의 cleanup 워크플로를 즉시 실행합니다 (정기 실행과 동일). 실행 중이면
            새로 누를 수 없습니다. 비운 공간은 DB 용량 수치에 바로 줄지 않습니다 — 아래 VACUUM
            FULL 안내를 보세요.
          </p>
        </div>
        <CleanupActions items={cleanupPresets} />
      </section>

      <section className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm dark:border-amber-800 dark:bg-amber-950/40">
        <h2 className="font-semibold">VACUUM FULL — 비운 공간을 실제로 돌려받기 (사무실 PC)</h2>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>
            <span className="font-medium">왜:</span> 정리 배치가 본문·응답 원문을 비워도
            PostgreSQL은 파일 크기를 줄이지 않습니다. Supabase 용량 수치에 반영되려면 VACUUM
            FULL로 raw_items·analyses 테이블을 다시 써야 합니다. 같은 실행이 카드 표(published_items)의
            색인도 무중단으로 다시 짓습니다 — 금고 정리 뒤 부푼 검색 색인을 돌려받습니다.
          </li>
          <li>
            <span className="font-medium">어떻게:</span> 사무실 PC에서 프로젝트 폴더의{" "}
            <code className="rounded bg-black/5 px-1 dark:bg-white/10">
              scripts\db_vacuum_full.bat
            </code>
            을 더블클릭합니다 (배치와 같은 scripts\.env의 DB 주소 사용, 전후 크기를 출력).
            GitHub Actions에서는 돌리지 않습니다 — 실행 시간이 길고 테이블을 통째로 잠급니다.
          </li>
          <li>
            <span className="font-medium">언제:</span> 배치가 없는 시간에만 — 화~금 09:00~12:00
            또는 16:00~21:00 권장. 03:30~07:30, 13:30~15:30, 22:00~23:30(KST)은 정리·분석·수집
            배치가, 월요일 오전은 주간 브리프 생성(09:17 예약이지만 실제로는 10:30~14:00에
            시작)이 돌아 겹치면 그쪽이 실패합니다. GitHub 예약은 1~4시간 늦게 시작하기도 하니
            실행 전 &ldquo;Gemini·Actions 사용 현황&rdquo;의 최근 배치 실행 표에 RUNNING이 없는지
            확인하세요. 5~10분 걸립니다.
          </li>
          <li>
            <span className="font-medium">순서:</span> 먼저 위 &ldquo;복제 컬럼 비우기&rdquo;가
            끝난 것을 확인한 뒤 실행해야 회수량이 큽니다. 끝나면 이 화면을 새로고침해 용량을
            확인하세요.
          </li>
        </ul>
      </section>

      <section>
        <h2 className="font-semibold">최근 정리 실행</h2>
        {(runsRes.data ?? []).length === 0 ? (
          <p className="mt-1 text-sm text-black/50 dark:text-white/50">실행 이력 없음</p>
        ) : (
          <div className="mt-2 overflow-x-auto rounded-lg border border-black/10 dark:border-white/15">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="border-b border-black/10 text-left text-black/60 dark:border-white/15 dark:text-white/60">
                <tr>
                  <th className="p-2">시작</th>
                  <th className="p-2">소요(초)</th>
                  <th className="p-2">상태</th>
                  <th className="p-2">결과 (작업별 건수)</th>
                </tr>
              </thead>
              <tbody>
                {(runsRes.data ?? []).map((r) => (
                  <tr
                    key={r.id}
                    className="border-b border-black/5 last:border-0 dark:border-white/10"
                  >
                    <td className="p-2 whitespace-nowrap">{formatDateTime(r.started_at)}</td>
                    <td className="p-2">{r.duration_seconds ?? "—"}</td>
                    <td
                      className={`p-2 ${
                        r.status === "SUCCESS"
                          ? "text-green-700 dark:text-green-400"
                          : r.status === "RUNNING"
                            ? ""
                            : "text-red-600"
                      }`}
                    >
                      {r.status}
                    </td>
                    <td className="p-2 text-xs text-black/60 dark:text-white/60">
                      {r.note ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
