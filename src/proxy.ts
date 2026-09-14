import { NextResponse, type NextRequest } from "next/server";
import { createServerClient } from "@supabase/ssr";

import { publicEnv } from "@/lib/env";

/**
 * 인증 프록시 (D-001) — Next.js 16의 proxy 파일 규약.
 * 열람은 익명 허용, /admin만 로그인(마스터 계정)을 요구한다.
 * 세션 쿠키 갱신은 @supabase/ssr 권장 패턴을 따른다.
 */
const ACCESS_COOKIE = "kiro_access";

/**
 * 배포 접근 게이트 (D-003): Vercel URL은 인터넷에 직접 노출되므로,
 * 상위 포털에 게시하는 링크(?access=토큰)를 통해서만 진입을 허용한다.
 * SITE_ACCESS_TOKEN이 설정되지 않은 환경(로컬 개발)에서는 비활성.
 */
function accessGate(request: NextRequest): NextResponse | null {
  const token = process.env.SITE_ACCESS_TOKEN;
  if (!token) return null;

  const { pathname } = request.nextUrl;
  if (pathname === "/blocked") return null;

  if (request.cookies.get(ACCESS_COOKIE)?.value === token) return null;

  const provided = request.nextUrl.searchParams.get("access");
  if (provided === token) {
    // 토큰 확인 → 쿠키 발급 후 토큰을 URL에서 제거
    const url = request.nextUrl.clone();
    url.searchParams.delete("access");
    const response = NextResponse.redirect(url);
    response.cookies.set(ACCESS_COOKIE, token, {
      httpOnly: true,
      secure: true,
      sameSite: "lax",
      maxAge: 60 * 60 * 24 * 180, // 180일
      path: "/",
    });
    return response;
  }

  const url = request.nextUrl.clone();
  url.pathname = "/blocked";
  url.search = "";
  return NextResponse.rewrite(url, { status: 403 });
}

export async function proxy(request: NextRequest) {
  const gated = accessGate(request);
  if (gated) return gated;

  let response = NextResponse.next({ request });

  const { supabaseUrl, supabaseAnonKey } = publicEnv();
  const supabase = createServerClient(supabaseUrl, supabaseAnonKey, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        cookiesToSet.forEach(({ name, value }) =>
          request.cookies.set(name, value),
        );
        response = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) =>
          response.cookies.set(name, value, options),
        );
      },
    },
  });

  const {
    data: { user },
  } = await supabase.auth.getUser();

  const { pathname } = request.nextUrl;

  // 관리 구역만 로그인 요구 (역할 검사는 admin 레이아웃에서 서버 측 수행)
  if (!user && pathname.startsWith("/admin")) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }

  if (user && pathname.startsWith("/login")) {
    const url = request.nextUrl.clone();
    url.pathname = "/admin";
    url.search = "";
    return NextResponse.redirect(url);
  }

  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|ico)$).*)"],
};
