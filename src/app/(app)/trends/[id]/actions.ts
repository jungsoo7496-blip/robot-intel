"use server";

import { createServiceRoleClient } from "@/lib/supabase/server";
import { getProfile } from "@/lib/auth";

const REPORT_TYPES = [
  "사실 오류",
  "잘못된 출처",
  "중복",
  "잘못된 분류",
  "과도한 해석",
  "부적절한 KIRO 시사점",
  "기타",
] as const;

/** 오류 신고 제출 (FR-014). 익명 열람 구조이므로 신고자 없이도 저장한다. */
export async function submitErrorReport(formData: FormData) {
  // 허니팟: 사람은 채우지 않는 숨은 필드 — 봇 스팸 차단 (외부 리뷰 2차)
  if (String(formData.get("website") ?? "")) {
    return { ok: true, message: "신고가 접수되었습니다." };
  }
  const publishedItemId = String(formData.get("published_item_id") ?? "");
  const reportType = String(formData.get("report_type") ?? "");
  const description = String(formData.get("description") ?? "").slice(0, 2000);

  if (!publishedItemId || !REPORT_TYPES.includes(reportType as never)) {
    return { ok: false, message: "신고 유형을 선택해 주세요." };
  }

  const profile = await getProfile();
  const supabase = createServiceRoleClient();
  const { error } = await supabase.from("error_reports").insert({
    published_item_id: publishedItemId,
    reported_by: profile?.id ?? null,
    report_type: reportType,
    description: description || null,
  });

  if (error) {
    return { ok: false, message: "신고 저장에 실패했습니다. 잠시 후 다시 시도해 주세요." };
  }
  return { ok: true, message: "신고가 접수되었습니다. 운영 책임자가 확인합니다." };
}
