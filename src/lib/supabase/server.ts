import { cookies } from "next/headers";
import { createServerClient } from "@supabase/ssr";
import { createClient as createSupabaseClient } from "@supabase/supabase-js";

import { publicEnv, serverEnv } from "@/lib/env";

/** 서버 컴포넌트·라우트 핸들러용 클라이언트 (사용자 세션 기반, RLS 적용). */
export async function createClient() {
  const cookieStore = await cookies();
  const { supabaseUrl, supabaseAnonKey } = publicEnv();

  return createServerClient(supabaseUrl, supabaseAnonKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options),
          );
        } catch {
          // 서버 컴포넌트에서 호출된 경우 — 미들웨어가 세션을 갱신하므로 무시 가능
        }
      },
    },
  });
}

/**
 * service role 클라이언트 — RLS를 우회한다.
 * 반드시 서버 측 역할 검사(requireOperator) 이후에만 사용한다 (NFR-004).
 */
export function createServiceRoleClient() {
  const { supabaseUrl, supabaseServiceRoleKey } = serverEnv();
  return createSupabaseClient(supabaseUrl, supabaseServiceRoleKey, {
    auth: { persistSession: false },
  });
}
