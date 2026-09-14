"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// 메뉴 순서는 사용자 지정 (2026-08-07): 홈 → 최신 동향 → 일간 → 주간 → 보고서 → 공고
const USER_MENU = [
  { href: "/", label: "홈" },
  { href: "/trends", label: "최신 동향" },
  { href: "/daily", label: "일간 리포트" },
  { href: "/briefs", label: "주간 리포트" },
  { href: "/reports", label: "정책·동향 보고서" },
  { href: "/policies", label: "R&D 공고" },
] as const;

/** 현재 위치를 표시하는 메뉴 링크 (UI/UX 개선 6). */
export function NavLinks({ isOperator }: { isOperator: boolean }) {
  const pathname = usePathname();

  function isActive(href: string) {
    if (href === "/") return pathname === "/";
    return pathname === href || pathname.startsWith(`${href}/`);
  }

  return (
    <nav className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm">
      {USER_MENU.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          aria-current={isActive(item.href) ? "page" : undefined}
          className={`rounded px-2 py-1 transition ${
            isActive(item.href)
              ? "bg-black/[0.07] font-semibold dark:bg-white/15"
              : "hover:bg-black/5 dark:hover:bg-white/10"
          }`}
        >
          {item.label}
        </Link>
      ))}
      {isOperator && (
        <Link
          href="/admin"
          aria-current={isActive("/admin") ? "page" : undefined}
          className={`rounded px-2 py-1 font-medium transition ${
            isActive("/admin")
              ? "bg-amber-200 text-amber-900 dark:bg-amber-800 dark:text-amber-100"
              : "bg-amber-100 text-amber-900 hover:bg-amber-200 dark:bg-amber-900/40 dark:text-amber-200"
          }`}
        >
          운영
        </Link>
      )}
    </nav>
  );
}
