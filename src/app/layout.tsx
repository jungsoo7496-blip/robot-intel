import type { Metadata } from "next";
import { Analytics } from "@vercel/analytics/next";
import "./globals.css";

export const metadata: Metadata = {
  title: "KIRO 로봇 인텔리전스",
  description:
    "공개 로봇 정책·산업·기술 정보를 AI가 수집·분석해 제공하는 KIRO 내부 플랫폼",
  // 내부용 사이트: URL 접근은 허용하되 검색엔진 색인은 차단한다 (robots.ts와 한 쌍).
  robots: {
    index: false,
    follow: false,
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body className="min-h-screen bg-background text-foreground antialiased">
        {children}
        {/* 방문자·페이지뷰 집계 (Vercel Web Analytics, 2026-09-22). 쿠키 없음, 개인 식별 없음.
            Vercel 프로젝트 > Analytics 탭에서 켜야 수집이 시작되고, 결과도 거기서 본다.
            로컬 개발 환경에서는 아무것도 보내지 않는다. */}
        <Analytics />
      </body>
    </html>
  );
}
