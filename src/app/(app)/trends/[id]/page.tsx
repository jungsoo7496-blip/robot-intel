import Link from "next/link";
import { notFound } from "next/navigation";

import { Badge, formatDate } from "@/components/item-card";
import { getItemDetail, getRelatedSources } from "@/lib/data";

import { ErrorReportForm } from "./error-report";

export const dynamic = "force-dynamic";

/**
 * 본문 섹션 — 사실/해석/시사점의 시각 계층을 구분한다 (UI/UX 개선 1).
 * 새 팔레트 없이 기존 색(중립·파랑·앰버)만 사용.
 */
function Section({
  title,
  variant = "plain",
  children,
}: {
  title: string;
  variant?: "plain" | "facts" | "implication" | "muted";
  children: React.ReactNode;
}) {
  const styles = {
    plain: "rounded-xl border border-black/10 dark:border-white/15",
    facts:
      "rounded-xl border border-black/10 border-l-4 border-l-blue-600/70 bg-black/[0.02] dark:border-white/15 dark:border-l-blue-400/70 dark:bg-white/[0.03]",
    implication:
      "rounded-xl border border-amber-300/70 bg-amber-50/60 dark:border-amber-700/50 dark:bg-amber-950/30",
    muted:
      "rounded-xl border border-dashed border-black/15 dark:border-white/20",
  } as const;

  return (
    <section className={`p-4 ${styles[variant]}`}>
      <h2
        className={`font-semibold ${
          variant === "muted" ? "text-black/60 dark:text-white/60" : ""
        }`}
      >
        {title}
      </h2>
      <div
        className={`mt-2 text-base leading-relaxed ${
          variant === "muted" ? "text-black/60 dark:text-white/60" : ""
        }`}
      >
        {children}
      </div>
    </section>
  );
}

/** 동향 상세 (FR-009): 사실·AI 해석·KIRO 시사점을 제목으로 구분해 표시. */
export default async function TrendDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const detail = await getItemDetail(id);
  if (!detail) notFound();
  const { item, analysis, policy, fromVault, vaultError } = detail;

  const relatedSources = await getRelatedSources(item);
  const representative = relatedSources.find((s) => s.isRepresentative);
  const others = relatedSources.filter((s) => !s.isRepresentative);

  // 분석이 없으면(금고 읽기 실패) 카드가 가진 요약으로 대신한다
  const summary = analysis?.one_line_summary ?? item.one_line_summary;

  const facts: string[] = Array.isArray(analysis?.verified_facts)
    ? analysis.verified_facts
    : [];
  const numbers: { label: string; value: string; source_basis?: string }[] =
    Array.isArray(analysis?.numbers_and_dates) ? analysis.numbers_and_dates : [];
  const keywords: string[] = Array.isArray(analysis?.keywords)
    ? analysis.keywords
    : [];
  const kiroAxes: string[] = Array.isArray(analysis?.kiro_relevance_axes)
    ? analysis.kiro_relevance_axes
    : [];
  const kiroWatchpoints: string[] = Array.isArray(analysis?.kiro_watchpoints)
    ? analysis.kiro_watchpoints
    : [];

  return (
    <article className="mx-auto max-w-3xl space-y-4">
      <div>
        {/* pill은 분류 3종만, 평가값은 보조 메타로 (UI/UX 개선 1) */}
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge>{item.category}</Badge>
          <Badge>{item.region}</Badge>
          <Badge>{item.robot_field}</Badge>
        </div>
        <h1 className="mt-2 text-2xl font-bold leading-snug">{item.title}</h1>
        <p className="mt-1 text-sm text-black/50 dark:text-white/50">
          {formatDate(item.source_published_at ?? item.published_at)} ·{" "}
          {item.representative_source_name ?? "출처 미상"} ·{" "}
          <a
            href={item.representative_url}
            target="_blank"
            rel="noopener noreferrer"
            className="underline"
          >
            원문 보기 ↗
          </a>
        </p>
        <p className="mt-0.5 text-sm text-black/45 dark:text-white/45">
          중요도 {item.importance} · 근거 {item.evidence_level} · KIRO 관련성{" "}
          {item.kiro_relevance}
        </p>
        {/* 오래된 기사는 부속이 보관 파일(금고)로 옮겨졌다 — 읽은 곳을 한 줄로 */}
        {fromVault && !vaultError && (
          <p className="mt-1 text-xs text-black/40 dark:text-white/40">
            <Badge>보관 파일에서 읽음</Badge>
          </p>
        )}
        {summary && (
          <p className="mt-3 text-[17px] leading-relaxed">{summary}</p>
        )}
      </div>

      {vaultError && (
        <div
          role="status"
          className="rounded-xl border border-amber-300/70 bg-amber-50/60 px-4 py-3 text-sm dark:border-amber-700/50 dark:bg-amber-950/30"
        >
          분석 내용을 불러오지 못했습니다. 제목·요약·원문 링크만 표시합니다.{" "}
          {/* 영구 실패(색인 없음·id 불일치·손상)는 기다려도 안 바뀐다 — 운영자가 봐야 한다 */}
          {vaultError.permanent
            ? "보관 파일에서 이 기사를 찾을 수 없습니다. 운영자에게 알려 주세요."
            : "일시적인 문제입니다. 잠시 후 다시 시도해 주세요."}
        </div>
      )}

      {/* 표시 순서: AI 해석 → 사실 → KIRO 시사점 → 수치·일정 → 한계 (사용자 지정) */}
      {analysis?.ai_interpretation && (
        <Section title="AI 해석">{analysis.ai_interpretation}</Section>
      )}

      {facts.length > 0 && (
        <Section title="확인된 사실" variant="facts">
          <ul className="list-disc space-y-1.5 pl-5">
            {facts.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
        </Section>
      )}

      {analysis?.kiro_implication && (
        <Section title="KIRO 시사점" variant="implication">
          {/* 실제 반환된 업무축만 칩으로 표시 (v1.1) */}
          {kiroAxes.length > 0 && (
            <div className="mb-2.5 flex flex-wrap gap-1.5">
              {kiroAxes.map((axis) => (
                <span
                  key={axis}
                  className="rounded-full bg-amber-200/70 px-2 py-0.5 text-xs font-medium text-amber-900 dark:bg-amber-800/60 dark:text-amber-100"
                >
                  {axis}
                </span>
              ))}
            </div>
          )}

          <p>{analysis.kiro_implication}</p>

          {kiroWatchpoints.length > 0 && (
            <div className="mt-3 border-t border-amber-300/50 pt-2.5 dark:border-amber-700/40">
              <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">
                향후 추적
              </p>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-[15px]">
                {kiroWatchpoints.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}

          {analysis.kiro_relevance_reason && (
            <p className="mt-2.5 text-xs text-black/45 dark:text-white/45">
              관련성 판단({item.kiro_relevance}): {analysis.kiro_relevance_reason}
            </p>
          )}
        </Section>
      )}

      {numbers.length > 0 && (
        <Section title="핵심 수치·일정">
          <ul className="space-y-1">
            {numbers.map((n, i) => (
              <li key={i}>
                <span className="font-medium">{n.label}:</span> {n.value}
                {n.source_basis && n.source_basis !== "원문" && (
                  <span className="text-black/50 dark:text-white/50">
                    {" "}
                    ({n.source_basis})
                  </span>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {analysis?.limitations && (
        <Section title="확인 필요·한계" variant="muted">
          {analysis.limitations}
        </Section>
      )}

      {policy && (
        <Section title="정책·사업 정보">
          <dl className="grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-2">
            {[
              ["정책명", policy.policy_name],
              ["사업명", policy.project_name],
              ["부처", policy.ministries?.join(", ")],
              ["기관", policy.organizations?.join(", ")],
              ["예산", policy.budget_text],
              [
                "사업기간",
                policy.project_start_date &&
                  `${policy.project_start_date} ~ ${policy.project_end_date ?? "미확인"}`,
              ],
              ["지원 대상", policy.support_targets?.join(", ")],
              ["상태", policy.announcement_status],
              ["지역", policy.target_region],
            ].map(([label, value]) => (
              <div key={label as string} className="flex gap-2">
                <dt className="shrink-0 font-medium">{label}</dt>
                <dd className="text-black/70 dark:text-white/70">
                  {value || "미확인"}
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-2">
            <Link href="/policies" className="underline">
              정책·R&D 메뉴에서 모아보기
            </Link>
          </p>
        </Section>
      )}

      <Section title="출처">
        {representative ? (
          <p>
            대표 출처:{" "}
            <a
              href={representative.url}
              target="_blank"
              rel="noopener noreferrer"
              className="underline"
            >
              {representative.sourceName} — {representative.title} ↗
            </a>
          </p>
        ) : (
          // 관련 출처를 못 읽어도 카드가 가진 대표 링크는 항상 보여준다
          <p>
            대표 출처:{" "}
            <a
              href={item.representative_url}
              target="_blank"
              rel="noopener noreferrer"
              className="underline"
            >
              {item.representative_source_name ?? "출처 미상"} — {item.title} ↗
            </a>
          </p>
        )}
        {others.length > 0 && (
          <>
            <p className="mt-2 font-medium">관련 출처 {others.length}건</p>
            <ul className="mt-1 list-disc space-y-1 pl-5">
              {others.map((s, i) => (
                <li key={i}>
                  <a
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline"
                  >
                    {s.sourceName} — {s.title} ↗
                  </a>
                </li>
              ))}
            </ul>
          </>
        )}
      </Section>

      <div className="flex flex-wrap items-center gap-2 text-xs text-black/50 dark:text-white/50">
        {keywords.map((k) => (
          <Badge key={k}>#{k}</Badge>
        ))}
      </div>

      {analysis && (
        <p className="text-xs text-black/40 dark:text-white/40">
          AI 분석: {analysis.model_name ?? "미상"} ·{" "}
          {formatDate(analysis.generated_at)} · 분석 결과는 자동 생성되며 원문
          확인을 권장합니다
        </p>
      )}

      <ErrorReportForm publishedItemId={item.id} />

      <p>
        <Link href="/trends" className="text-sm underline">
          ← 최신 동향으로
        </Link>
      </p>
    </article>
  );
}
