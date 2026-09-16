"use client";

import { useActionState } from "react";

import { requestReanalysis } from "../actions";

/**
 * AI 재분석 버튼 — 서버 액션의 {ok, message} 를 버튼 옆에 문장으로 보여준다.
 *
 * 클라이언트 컴포넌트인 이유: 서버 컴포넌트의 <form action={서버액션}> 은 반환값을
 * 버리므로, 거절 사유(보관 파일로 옮긴 기사 등, 계약 C12)를 운영자가 볼 수 없다.
 * useActionState 로 결과를 받아 그 자리에 표시한다. 보관된 카드는 부모가 이 버튼을
 * 아예 그리지 않는다 — 여기 문구는 그 사이(화면을 연 뒤 배치가 옮긴 경우)의 안전망.
 */
export function ReanalyzeButton({
  publishedItemId,
  className,
}: {
  publishedItemId: string;
  className: string;
}) {
  const [state, formAction, pending] = useActionState(requestReanalysis, null);

  return (
    <form action={formAction} className="flex flex-wrap items-center gap-2">
      <input type="hidden" name="published_item_id" value={publishedItemId} />
      <button className={className} disabled={pending}>
        {pending ? "요청 중…" : "AI 재분석"}
      </button>
      {state && (
        <span
          role="status"
          className={`text-xs ${
            state.ok
              ? "text-green-700 dark:text-green-400"
              : "text-red-600 dark:text-red-400"
          }`}
        >
          {state.message}
        </span>
      )}
    </form>
  );
}
