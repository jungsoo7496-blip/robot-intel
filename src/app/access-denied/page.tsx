import Link from "next/link";

/** USER의 운영 메뉴 접근 차단 화면 (FR-001). */
export default function AccessDeniedPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4 text-center">
      <h1 className="text-xl font-bold">접근 권한이 없습니다</h1>
      <p className="mt-3 text-sm text-black/70 dark:text-white/70">
        이 페이지는 운영 책임자 전용입니다.
      </p>
      <Link href="/" className="mt-6 text-sm underline">
        홈으로 돌아가기
      </Link>
    </main>
  );
}
