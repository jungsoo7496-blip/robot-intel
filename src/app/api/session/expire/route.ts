import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

/**
 * 운영자 세션 24시간 만료 처리 (사용자 요청 2026-08-09).
 * 서버 컴포넌트(requireOperator)는 쿠키를 못 지우므로 여기서 로그아웃한다.
 */
export async function GET() {
  const supabase = await createClient();
  await supabase.auth.signOut();
  redirect("/login?next=/admin&expired=1");
}
