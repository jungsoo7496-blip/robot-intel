import Link from "next/link";

import { SiteNav } from "@/components/site-nav";
import { getProfile } from "@/lib/auth";

/**
 * 공통 레이아웃 (D-001): 열람은 로그인 없이 가능.
 * 운영자로 로그인한 경우에만 운영 메뉴가 나타난다.
 */
export default async function AppLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const profile = await getProfile();

  return (
    <div className="flex min-h-screen flex-col">
      <SiteNav profile={profile} />
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
        {children}
      </main>
      <footer className="border-t border-black/10 py-4 text-center text-xs leading-relaxed text-black/50 dark:border-white/15 dark:text-white/50">
        공개자료 기반 AI 자동 분석 · 사실관계는 원문에서 확인하세요 ·
        KIRO 시사점은 AI가 생성한 검토용 제안이며 기관의 공식 입장이 아닙니다 ·{" "}
        <Link href="/admin" className="underline hover:text-black/70 dark:hover:text-white/70">
          운영자
        </Link>
      </footer>
    </div>
  );
}
