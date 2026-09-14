import { NextResponse } from "next/server";

import { createClient } from "@/lib/supabase/server";

/** 이메일 인증·매직 링크 콜백 — 코드 교환 후 홈으로 이동. */
export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");

  if (code) {
    const supabase = await createClient();
    await supabase.auth.exchangeCodeForSession(code);
  }
  return NextResponse.redirect(`${origin}/`);
}
