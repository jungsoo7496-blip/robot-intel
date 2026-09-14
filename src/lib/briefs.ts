import "server-only";

import { createClient } from "@/lib/supabase/server";

export type BriefListRow = {
  id: string;
  title: string;
  version: number;
  is_current: boolean;
  published_at: string | null;
  generated_at: string | null;
  section_summary_policy: { one_page_summary?: string } | null;
  brief_periods: {
    period_start: string;
    period_end: string;
    cadence: "WEEKLY" | "BIWEEKLY";
  } | null;
  brief_items: { count: number }[];
};

export type BriefSections = {
  s1: {
    one_page_summary?: string;
    policy_headline?: string;
    key_changes?: string[];
    policy_domestic?: string;
    policy_us?: string;
    policy_china?: string;
    policy_japan?: string;
    policy_europe_etc?: string;
  } | null;
  s2: {
    industry_headline?: string;
    industry_overseas?: string;
    industry_domestic?: string;
    tech_headline?: string;
    tech_trends?: string;
  } | null;
  s3: {
    changes_from_previous?: string;
    kiro_implications?: string;
    tracking_items?: string[];
  } | null;
};

/** 발행 브리프 목록 (현재 버전만, 기간 내림차순). */
export async function getBriefs(): Promise<BriefListRow[]> {
  const supabase = await createClient();
  const { data } = await supabase
    .from("briefs")
    .select(
      "id, title, version, is_current, published_at, generated_at, section_summary_policy, brief_periods(period_start, period_end, cadence), brief_items(count)",
    )
    .eq("status", "PUBLISHED")
    .eq("is_current", true)
    .order("published_at", { ascending: false });
  return (data ?? []) as unknown as BriefListRow[];
}

/** 브리프 상세 + 근거자료 + 이전·다음 이동 (tasks §13.6). */
export async function getBriefDetail(id: string) {
  const supabase = await createClient();
  const { data: brief } = await supabase
    .from("briefs")
    .select("*, brief_periods(period_start, period_end, cadence)")
    .eq("id", id)
    .maybeSingle();
  if (!brief) return null;

  const [{ data: items }, { data: versions }] = await Promise.all([
    supabase
      .from("brief_items")
      .select(
        "display_order, section, is_low_confidence, published_item_id, title_snapshot, summary_snapshot, facts_snapshot, source_name_snapshot, source_url_snapshot",
      )
      .eq("brief_id", id)
      .order("display_order"),
    supabase
      .from("briefs")
      .select("id, version, is_current, generated_at")
      .eq("brief_period_id", brief.brief_period_id)
      .order("version", { ascending: false }),
  ]);

  // 이전·다음 브리프 (현재 버전 기준, 기간순)
  const all = await getBriefs();
  const idx = all.findIndex((b) => b.id === id);
  const prev = idx >= 0 && idx < all.length - 1 ? all[idx + 1] : null;
  const next = idx > 0 ? all[idx - 1] : null;

  const sections: BriefSections = {
    s1: brief.section_summary_policy ?? null,
    s2: brief.section_industry_tech ?? null,
    s3: brief.section_outlook ?? null,
  };

  return {
    brief,
    sections,
    // 부록은 발행 당시 스냅샷만 사용한다 — 이후 재분석·숨김과 무관 (FR-013)
    items: (items ?? []) as unknown as {
      display_order: number;
      section: string;
      is_low_confidence: boolean;
      published_item_id: string;
      title_snapshot: string | null;
      summary_snapshot: string | null;
      facts_snapshot: string[] | null;
      source_name_snapshot: string | null;
      source_url_snapshot: string | null;
    }[],
    versions: versions ?? [],
    prev,
    next,
  };
}

/** 아카이브: 연·월별 브리프와 동향 수 (FR-013). */
export async function getArchiveMonths() {
  const supabase = await createClient();
  const [{ data: briefs }, { data: items }] = await Promise.all([
    supabase
      .from("briefs")
      .select("id, title, published_at, brief_periods(period_start, period_end)")
      .eq("status", "PUBLISHED")
      .eq("is_current", true)
      .order("published_at", { ascending: false }),
    supabase
      .from("published_items")
      .select("id, published_at")
      .eq("is_visible", true),
  ]);

  const months = new Map<
    string,
    { briefs: typeof briefs; itemCount: number }
  >();
  for (const b of briefs ?? []) {
    const key = (b.published_at ?? "").slice(0, 7);
    if (!key) continue;
    if (!months.has(key)) months.set(key, { briefs: [], itemCount: 0 });
    months.get(key)!.briefs!.push(b);
  }
  for (const item of items ?? []) {
    const key = (item.published_at ?? "").slice(0, 7);
    if (!key) continue;
    if (!months.has(key)) months.set(key, { briefs: [], itemCount: 0 });
    months.get(key)!.itemCount += 1;
  }
  return [...months.entries()]
    .sort(([a], [b]) => b.localeCompare(a))
    .map(([month, v]) => ({ month, briefs: v.briefs ?? [], itemCount: v.itemCount }));
}
