import Link from "next/link";
import { notFound } from "next/navigation";

import { formatDate } from "@/components/item-card";
import { getBriefDetail } from "@/lib/briefs";

export const dynamic = "force-dynamic";

/** [n] 인용을 부록 항목(#ref-n) 링크로 변환한다 (사용자 피드백). */
function withCitations(text: string): React.ReactNode[] {
  const parts = text.split(/(\[\d+\])/g);
  return parts.map((part, i) => {
    const m = part.match(/^\[(\d+)\]$/);
    if (!m) return part;
    return (
      <a
        key={i}
        href={`#ref-${m[1]}`}
        className="mx-0.5 rounded bg-black/[0.06] px-1 text-xs font-medium text-black/60 no-underline hover:bg-black/[0.12] dark:bg-white/10 dark:text-white/60 dark:hover:bg-white/20"
      >
        {m[1]}
      </a>
    );
  });
}

/**
 * 개조식 텍스트 렌더러 (사용자 피드백):
 * 원문 기호(□/ㅇ/※)를 그대로 노출하지 않고 스타일된 계층으로 표현한다.
 */
function BulletText({ text }: { text?: string }) {
  const value = text?.trim() || "이번 기간 특기할 동향 없음";
  const lines = value.split("\n").filter((l) => l.trim().length > 0);

  return (
    <div className="space-y-1 text-base leading-7">
      {lines.map((raw, i) => {
        const line = raw.trim();

        if (line.startsWith("□")) {
          return (
            <p key={i} className="mt-4 flex gap-2 font-semibold first:mt-0">
              <span className="mt-[7px] block h-2 w-2 shrink-0 rounded-sm bg-blue-600 dark:bg-blue-400" />
              <span>{withCitations(line.replace(/^□\s*/, ""))}</span>
            </p>
          );
        }
        if (line.startsWith("ㅇ") || line.startsWith("○")) {
          return (
            <p key={i} className="flex gap-2 pl-5 text-black/75 dark:text-white/75">
              <span className="mt-[9px] block h-1 w-1 shrink-0 rounded-full bg-black/40 dark:bg-white/40" />
              <span>{withCitations(line.replace(/^[ㅇ○]\s*/, ""))}</span>
            </p>
          );
        }
        if (line.startsWith("※") || line.startsWith("-") || line.startsWith("*")) {
          return (
            <p key={i} className="pl-9 text-sm text-black/50 dark:text-white/50">
              {withCitations(line.replace(/^[-*]\s*/, ""))}
            </p>
          );
        }
        if (/^【.+】$/.test(line)) {
          return (
            <p key={i} className="mt-4 text-sm font-bold tracking-wide text-black/50 first:mt-0 dark:text-white/50">
              {line.replace(/[【】]/g, "")}
            </p>
          );
        }
        if (/^[①②③④⑤]/.test(line)) {
          return (
            <p
              key={i}
              className="mt-2 rounded-lg bg-black/[0.03] px-3 py-2 first:mt-0 dark:bg-white/[0.05]"
            >
              {withCitations(line)}
            </p>
          );
        }
        return <p key={i}>{withCitations(line)}</p>;
      })}
    </div>
  );
}

function Headline({ text }: { text?: string }) {
  if (!text?.trim()) return null;
  return (
    <p className="mb-3 rounded-lg bg-black/[0.04] px-3 py-2 font-semibold dark:bg-white/[0.06]">
      {text}
    </p>
  );
}

function H2({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2
      id={id}
      className="mb-3 mt-10 scroll-mt-20 border-b-2 border-black/15 pb-1.5 text-xl font-bold dark:border-white/20"
    >
      {children}
    </h2>
  );
}

function H3({ children }: { children: React.ReactNode }) {
  return <h3 className="mb-1.5 mt-5 font-bold text-black/80 dark:text-white/80">{children}</h3>;
}

const TOC = [
  ["summary", "1. 한 페이지 요약"],
  ["policy", "2. 정책 동향"],
  ["industry", "3. 산업 동향"],
  ["tech", "4. 기술 동향"],
  ["changes", "5. 전주 대비 변화"],
  ["implications", "6. KIRO 시사점"],
  ["tracking", "7. 다음 주 추적"],
  ["appendix", "부록. 동향별 상세"],
] as const;

/** 브리프 상세 (tasks §13.6): KIRO 보고서 골격 + 개조식 + 부록. */
export default async function BriefDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const detail = await getBriefDetail(id);
  if (!detail || detail.brief.status !== "PUBLISHED") notFound();
  const { brief, sections, items, versions, prev, next } = detail;
  const { s1, s2, s3 } = sections;

  // 부록: 분야별 그룹
  const byCategory = new Map<string, typeof items>();
  for (const it of items) {
    if (!byCategory.has(it.section)) byCategory.set(it.section, []);
    byCategory.get(it.section)!.push(it);
  }

  return (
    // 주의: items-start를 주면 aside가 내용 높이로 줄어 sticky가 못 따라온다
    <div className="mx-auto flex max-w-5xl gap-8">
      {/* 데스크톱 sticky 목차 (UI/UX 개선 5) — aside는 본문 높이만큼 stretch */}
      <aside className="hidden w-48 shrink-0 self-stretch lg:block">
        <nav className="sticky top-6 rounded-xl border border-black/10 p-3 text-sm dark:border-white/12">
          <p className="mb-2 font-semibold">목차</p>
          <ul className="space-y-1.5">
            {TOC.map(([anchor, label]) => (
              <li key={anchor}>
                <a
                  href={`#${anchor}`}
                  className="block leading-snug text-black/65 underline-offset-2 hover:text-black hover:underline dark:text-white/65 dark:hover:text-white"
                >
                  {label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </aside>

      <article className="min-w-0 max-w-3xl flex-1">
        <div className="text-sm text-black/50 dark:text-white/50">
          {brief.brief_periods
            ? `대상 기간 ${brief.brief_periods.period_start} ~ ${brief.brief_periods.period_end}`
            : ""}
          {" · "}발행 {formatDate(brief.published_at)}
          {brief.brief_periods?.cadence === "BIWEEKLY" && (
            <span className="ml-2 rounded bg-black/5 px-1.5 py-0.5 text-xs font-medium text-black/60 dark:bg-white/10 dark:text-white/60">
              격주 발행분
            </span>
          )}
          {!brief.is_current && (
            <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-900 dark:bg-amber-900/40 dark:text-amber-200">
              과거 버전 v{brief.version}
            </span>
          )}
        </div>
        <h1 className="mt-1 text-2xl font-bold">{brief.title}</h1>

        {/* 모바일 접는 목차 */}
        <details className="mt-4 rounded-xl border border-black/10 p-3 text-sm dark:border-white/12 lg:hidden">
          <summary className="cursor-pointer font-medium">목차</summary>
          <ul className="mt-2 space-y-1.5">
            {TOC.map(([anchor, label]) => (
              <li key={anchor}>
                <a
                  href={`#${anchor}`}
                  className="underline-offset-2 hover:underline"
                >
                  {label}
                </a>
              </li>
            ))}
          </ul>
        </details>

      <H2 id="summary">1. 한 페이지 요약</H2>
      <BulletText text={s1?.one_page_summary} />
      {s1?.key_changes && s1.key_changes.length > 0 && (
        <>
          <H3>이번 기간 핵심 변화</H3>
          <div className="space-y-2">
            {s1.key_changes.map((c, i) => (
              <BulletText key={i} text={c} />
            ))}
          </div>
        </>
      )}

      <H2 id="policy">2. 정책 동향</H2>
      <Headline text={s1?.policy_headline} />
      <H3>국내</H3>
      <BulletText text={s1?.policy_domestic} />
      <H3>미국</H3>
      <BulletText text={s1?.policy_us} />
      <H3>중국</H3>
      <BulletText text={s1?.policy_china} />
      <H3>일본</H3>
      <BulletText text={s1?.policy_japan} />
      <H3>유럽·기타</H3>
      <BulletText text={s1?.policy_europe_etc} />

      <H2 id="industry">3. 산업 동향</H2>
      <Headline text={s2?.industry_headline} />
      <H3>해외</H3>
      <BulletText text={s2?.industry_overseas} />
      <H3>국내</H3>
      <BulletText text={s2?.industry_domestic} />

      <H2 id="tech">4. 기술 동향</H2>
      <Headline text={s2?.tech_headline} />
      <BulletText text={s2?.tech_trends} />

      <H2 id="changes">5. 전주 대비 변화</H2>
      <BulletText text={s3?.changes_from_previous} />

      <H2 id="implications">6. KIRO 시사점</H2>
      <div className="rounded-xl border border-amber-300/60 bg-amber-50/60 p-4 dark:border-amber-700/50 dark:bg-amber-950/30">
        <BulletText text={s3?.kiro_implications} />
      </div>

      <H2 id="tracking">7. 다음 주 추적 항목</H2>
      {s3?.tracking_items && s3.tracking_items.length > 0 ? (
        <ul className="list-disc space-y-1.5 pl-5 text-[15px] leading-relaxed">
          {s3.tracking_items.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      ) : (
        <p className="text-[15px]">이번 기간 특기할 항목 없음</p>
      )}

      <H2 id="appendix">부록. 동향별 상세</H2>
      <p className="text-sm text-black/55 dark:text-white/55">
        이번 브리프에 사용된 전체 동향의 요약과 확인된 사실입니다 (발행 당시
        스냅샷). 제목을 누르면 현재의 상세 분석으로 이동합니다.
      </p>
      {[...byCategory.entries()].map(([category, group]) => (
        <section key={category} className="mt-5">
          <H3>{category}</H3>
          <div className="space-y-3">
            {group.map((it) => (
              <div
                key={it.display_order}
                id={`ref-${it.display_order}`}
                className="scroll-mt-20 rounded-xl border border-black/10 p-4 dark:border-white/12"
              >
                <p className="font-semibold leading-snug">
                  <span className="mr-1.5 rounded bg-black/[0.06] px-1.5 py-0.5 text-xs text-black/60 dark:bg-white/10 dark:text-white/60">
                    {it.display_order}
                  </span>{" "}
                  <Link
                    href={`/trends/${it.published_item_id}`}
                    className="underline-offset-2 hover:underline"
                  >
                    {it.title_snapshot ?? "(제목 미보존)"}
                  </Link>
                  {it.is_low_confidence && (
                    <span className="ml-1.5 text-xs font-normal text-amber-700 dark:text-amber-400">
                      (근거 약함 — 확인 필요)
                    </span>
                  )}
                </p>
                {it.summary_snapshot && (
                  <p className="mt-1.5 text-[15px] leading-relaxed text-black/80 dark:text-white/80">
                    {it.summary_snapshot}
                  </p>
                )}
                {(it.facts_snapshot ?? []).slice(0, 3).map((f, i) => (
                  <p
                    key={i}
                    className="mt-1 flex gap-2 pl-1 text-sm leading-relaxed text-black/65 dark:text-white/65"
                  >
                    <span className="mt-[8px] block h-1 w-1 shrink-0 rounded-full bg-black/35 dark:bg-white/35" />
                    <span>{f}</span>
                  </p>
                ))}
                <p className="mt-1.5 text-xs text-black/45 dark:text-white/45">
                  출처: {it.source_name_snapshot ?? "미상"}{" "}
                  {it.source_url_snapshot && (
                    <a
                      href={it.source_url_snapshot}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline"
                    >
                      원문 ↗
                    </a>
                  )}
                </p>
              </div>
            ))}
          </div>
        </section>
      ))}

      <div className="mt-8 rounded-xl border border-black/10 p-3 text-xs text-black/50 dark:border-white/12 dark:text-white/50">
        AI 자동 생성 브리프 · 모델 {brief.model_name} · 생성{" "}
        {formatDate(brief.generated_at)} · 프롬프트 {brief.prompt_version} ·
        발행 당시 상태로 보존됩니다
        {versions.length > 1 && (
          <span>
            {" · 버전: "}
            {versions.map((v, i) => (
              <span key={v.id}>
                {i > 0 && ", "}
                {v.id === brief.id ? (
                  <strong>v{v.version}</strong>
                ) : (
                  <Link href={`/briefs/${v.id}`} className="underline">
                    v{v.version}
                  </Link>
                )}
              </span>
            ))}
          </span>
        )}
      </div>

        <nav className="mt-6 flex justify-between text-sm">
          {prev ? (
            <Link href={`/briefs/${prev.id}`} className="underline">
              ← 이전 브리프
            </Link>
          ) : (
            <span />
          )}
          {next ? (
            <Link href={`/briefs/${next.id}`} className="underline">
              다음 브리프 →
            </Link>
          ) : (
            <span />
          )}
        </nav>
      </article>
    </div>
  );
}
