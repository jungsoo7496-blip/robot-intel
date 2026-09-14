import { requireOperator } from "@/lib/auth";

/** 운영 구역 — 서버 측 역할 검사로 URL 직접 접근도 차단한다 (FR-001). */
export default async function AdminLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  await requireOperator();
  return <>{children}</>;
}
