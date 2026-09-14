import "server-only";

import { createServiceRoleClient } from "@/lib/supabase/server";

/** NTIS R&D 공고 (0015·0016, collect_rnd.py가 수집). */
export type RndAnnouncement = {
  id: string;
  board: string;
  agency: string | null; // 부처명
  org_name: string | null; // 공고기관명 (전문기관)
  title: string;
  source_url: string;
  posted_date: string | null;
  apply_start: string | null; // 접수 시작일
  deadline_date: string | null; // null = 상시 등
  d_day_label: string;
  notice_type: string | null; // 신규과제·수요조사 등
  budget_text: string | null;
  description: string | null; // 공고내용 본문 요약
  status_label: string | null; // 접수중 | 접수예정
  matched_keywords: string[];
};

function kstTodayStr(): string {
  return new Date(Date.now() + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

/** 접수 중·예정(마감 미도래 또는 상시)인 로봇 관련 공고 — 마감 임박 순. */
export async function getRobotAnnouncements(limit = 30) {
  // 공개 데이터 읽기 전용 — 쿠키 비의존이라 unstable_cache 안에서도 안전
  const supabase = createServiceRoleClient();
  const today = kstTodayStr();

  const { data, error } = await supabase
    .from("rnd_announcements")
    .select("*")
    .eq("is_robot_related", true)
    .or(`deadline_date.gte.${today},deadline_date.is.null`)
    .order("deadline_date", { ascending: true, nullsFirst: false })
    .limit(limit);
  if (error) throw error;
  return { rows: (data ?? []) as RndAnnouncement[], today };
}

/**
 * 로봇 관련 공고 아카이브 (사용자 요청 2026-08-08 — "그냥 쌓아놓고 싶어").
 * 접수 중이 위, 마감된 과거 공고가 아래로 이어지는 단일 표 —
 * 재공고·차년도 공고 예측용으로 삭제 없이 계속 누적된다.
 * 정렬: 마감일 내림차순(상시 최상단) = 접수중 → 최근 마감 → 과거 순.
 */
export async function getRobotAnnouncementArchive(page = 1, pageSize = 30) {
  const supabase = createServiceRoleClient();
  const today = kstTodayStr();

  const { data, count, error } = await supabase
    .from("rnd_announcements")
    .select("*", { count: "exact" })
    .eq("is_robot_related", true)
    .order("deadline_date", { ascending: false, nullsFirst: true })
    .range((page - 1) * pageSize, page * pageSize - 1);
  if (error) throw error;
  return {
    rows: (data ?? []) as RndAnnouncement[],
    total: count ?? 0,
    today,
    page,
    pageSize,
  };
}

/**
 * 마감 임박 공고 — D-7 이내만 (사용자 지시 2026-08-09: 일간 리포트에
 * 같은 공고가 매일 반복되지 않게, '임박'답게 7일 창으로 제한).
 * 상시(마감 없음) 공고는 임박이 아니므로 제외.
 */
export async function getImminentAnnouncements(days = 7, limit = 8) {
  const supabase = createServiceRoleClient();
  const today = kstTodayStr();
  const until = new Date(Date.parse(today) + days * 24 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 10);

  const { data, error } = await supabase
    .from("rnd_announcements")
    .select("*")
    .eq("is_robot_related", true)
    .gte("deadline_date", today)
    .lte("deadline_date", until)
    .order("deadline_date", { ascending: true })
    .limit(limit);
  if (error) throw error;
  return { rows: (data ?? []) as RndAnnouncement[], today };
}

/** 오늘 기준 남은 일수 (수집 시점이 아니라 열람 시점 기준으로 재계산). */
export function daysLeft(deadline: string | null, today: string): number | null {
  if (!deadline) return null;
  const dayMs = 24 * 60 * 60 * 1000;
  return Math.round((Date.parse(deadline) - Date.parse(today)) / dayMs);
}

/**
 * 공고 본문 표시용 정리: NTIS 상세의 '공고내용'은 제목·접수일·연락처가
 * 반복되는 머리말로 시작하므로 표시할 때 걷어낸다.
 */
export function tidyDescription(
  description: string | null,
  title: string,
): string | null {
  if (!description) return null;
  let text = description;
  // HTML 엔티티
  text = text
    .replace(/&nbsp;/g, " ")
    .replace(/&middot;/g, "·")
    .replace(/&amp;/g, "&");
  // NTIS 꼬리말 이후는 전부 절단
  for (const marker of ["최종 수정일", "COPYRIGHT", "개인정보처리방침"]) {
    const i = text.indexOf(marker);
    if (i >= 0) text = text.slice(0, i);
  }
  // 반복 안내문·머리말 제거
  text = text.replace(/※\s*자세한 내용은[^.]*바랍니다\.?/g, " ");
  text = text.replace(/-->|닫기/g, " ");
  text = text.replace(title, " ");
  text = text.replace(/^\s*공고\s*/, "");
  text = text.replace(/\b\d{8}\b/g, " ");
  text = text.replace(/접\s*수\s*일\s*:?[\s~\d.]*/g, " ");
  text = text.replace(/마감시간\s*:?\s*\d{0,4}\s*까지/g, " ");
  text = text.replace(/연\s*락\s*처\s*:?/g, " ");
  text = text.replace(/\s+/g, " ").trim();
  return text.length >= 20 ? text : null;
}
