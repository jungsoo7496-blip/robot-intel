/**
 * 환경변수 검증 모듈 (tasks §1.2).
 * 누락 시 모호한 런타임 오류 대신 명확한 메시지로 실패한다.
 */

function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(
      `필수 환경변수가 없습니다: ${name} — .env.local 또는 배포 환경 설정을 확인하세요.`,
    );
  }
  return value;
}

/** 브라우저에 노출 가능한 공개 설정 */
export function publicEnv() {
  return {
    supabaseUrl: required(
      "NEXT_PUBLIC_SUPABASE_URL",
      process.env.NEXT_PUBLIC_SUPABASE_URL,
    ),
    supabaseAnonKey: required(
      "NEXT_PUBLIC_SUPABASE_ANON_KEY",
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
    ),
  };
}

/** 서버 전용 설정 — 브라우저 번들에 포함 금지 (NFR-004) */
export function serverEnv() {
  return {
    ...publicEnv(),
    supabaseServiceRoleKey: required(
      "SUPABASE_SERVICE_ROLE_KEY",
      process.env.SUPABASE_SERVICE_ROLE_KEY,
    ),
  };
}
