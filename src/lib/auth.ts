import { cache } from "react";

import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

export type Profile = {
  id: string;
  email: string;
  display_name: string | null;
  role: "USER" | "OPERATOR";
  is_active: boolean;
};

/** 현재 로그인 사용자의 프로필. 비로그인(익명 열람) 시 null. */
export async function getProfile(): Promise<Profile | null> {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return null;

  const { data } = await supabase
    .from("profiles")
    .select("id, email, display_name, role, is_active")
    .eq("id", user.id)
    .single();
  return (data as Profile) ?? null;
}

// 운영자 세션 절대 수명 (사용자 요청 2026-08-09): 브라우저를 닫아도
// 세션이 유지되는 건 정상(신뢰 기기 로그인 유지)이지만, 로그인 후
// 24시간이 지나면 재로그인을 요구해 방치된 세션을 잘라낸다.
const OPERATOR_SESSION_MAX_MS = 24 * 60 * 60 * 1000;

/**
 * 운영 책임자 필수 (D-001) — /admin은 마스터 OPERATOR 계정만 접근한다.
 * URL 직접 접근도 서버 측에서 차단된다.
 *
 * React cache()로 감싼 이유: layout 검사에만 기대지 말고 각 페이지·액션이
 * 스스로 호출해야 안전한데(레이아웃은 렌더 순서를 보장하지 않는다), 그러면
 * 한 요청에서 Supabase 인증·profiles 조회가 여러 번 반복된다. 요청 단위로
 * 결과를 재사용해 호출을 늘려도 왕복이 늘지 않게 한다.
 * 리다이렉트도 그대로 유지된다 — redirect()가 던지는 오류는 cache가 그대로
 * 다시 던지므로 만료 시 /api/session/expire 이동은 변하지 않는다.
 */
export const requireOperator = cache(async (): Promise<Profile> => {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login?next=/admin");

  // last_sign_in_at은 비밀번호 로그인 시각 — 토큰 자동 갱신으로는 안 바뀐다
  const signedInAt = user.last_sign_in_at
    ? Date.parse(user.last_sign_in_at)
    : 0;
  if (!signedInAt || Date.now() - signedInAt > OPERATOR_SESSION_MAX_MS) {
    // 서버 컴포넌트에서는 쿠키를 못 지우므로 라우트 핸들러가 로그아웃 처리
    redirect("/api/session/expire");
  }

  const profile = await getProfile();
  if (!profile) redirect("/login?next=/admin");
  if (profile.role !== "OPERATOR" || !profile.is_active) {
    redirect("/access-denied");
  }
  return profile;
});
