import Link from "next/link";

import { NavLinks } from "@/components/nav-links";
import type { Profile } from "@/lib/auth";

/**
 * 상단 메뉴 (설계 §15, D-001, UI/UX 개선 6):
 * - 검색바 상시 노출, 현재 메뉴 active 표시
 * - 운영자로 로그인한 경우에만 운영 메뉴·로그아웃 표시
 */
export function SiteNav({ profile }: { profile: Profile | null }) {
  const isOperator = profile?.role === "OPERATOR";

  return (
    <header className="border-b border-black/10 dark:border-white/15">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3">
        <Link href="/" className="text-lg font-bold tracking-tight">
          KIRO 로봇 인텔리전스
        </Link>

        <NavLinks isOperator={isOperator} />

        <form action="/search" className="ml-auto flex min-w-40 max-w-xs flex-1">
          <input
            type="search"
            name="q"
            placeholder="검색 — 예: 휴머노이드"
            className="w-full rounded-l-lg border border-r-0 border-black/15 bg-transparent px-3 py-1.5 text-sm outline-none focus:border-black/40 dark:border-white/20 dark:focus:border-white/50"
          />
          <button
            type="submit"
            aria-label="검색"
            className="flex items-center rounded-r-lg border border-black/15 px-3 hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
          >
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              className="text-black/55 dark:text-white/55"
            >
              <circle cx="11" cy="11" r="7" />
              <line x1="16.5" y1="16.5" x2="21" y2="21" />
            </svg>
          </button>
        </form>

        {isOperator && (
          <div className="flex items-center gap-3 text-sm text-black/60 dark:text-white/60">
            <span>{profile.display_name ?? profile.email}</span>
            <form action="/auth/signout" method="post">
              <button
                type="submit"
                className="rounded border border-black/15 px-2 py-1 hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
              >
                로그아웃
              </button>
            </form>
          </div>
        )}
      </div>
    </header>
  );
}
