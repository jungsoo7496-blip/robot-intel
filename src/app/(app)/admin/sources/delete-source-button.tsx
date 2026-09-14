"use client";

import { useActionState } from "react";

import { deleteSource } from "./actions";

type Props = {
  id: string;
  name: string;
  /** 이 수집원의 raw_items 건수 — 0이면 삭제, 있으면 비활성화 */
  rawCount: number;
  isActive: boolean;
};

/** 행별 '삭제' 버튼. 결과는 서버 액션이 목록 상단 안내(notice)로 돌려준다. */
export function DeleteSourceButton({ id, name, rawCount, isActive }: Props) {
  const [state, formAction, pending] = useActionState(deleteSource, null);
  const countText = rawCount.toLocaleString("ko-KR");

  const onSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    if (rawCount > 0 && !isActive) {
      window.alert(
        `‘${name}’은(는) 수집 기록 ${countText}건이 있어 삭제할 수 없고 이미 비활성 상태입니다. 기록을 지키기 위해 그대로 둡니다.`,
      );
      e.preventDefault();
      return;
    }
    const text =
      rawCount > 0
        ? `‘${name}’에는 수집 기록 ${countText}건이 있어 삭제 대신 비활성화합니다. 이미 수집된 기사는 남습니다. 계속할까요?`
        : `‘${name}’을(를) 삭제할까요? 수집 기록이 없어 바로 지워지며 되돌릴 수 없습니다.`;
    if (!window.confirm(text)) e.preventDefault();
  };

  return (
    <form action={formAction} onSubmit={onSubmit}>
      <input type="hidden" name="id" value={id} />
      <button
        type="submit"
        disabled={pending}
        className="whitespace-nowrap rounded border border-red-300 px-2 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-950/40"
      >
        {pending ? "처리 중…" : "삭제"}
      </button>
      {/* 표 칸이 좁으므로(table-fixed) 폭을 넘기지 않고 줄바꿈으로 흘린다 */}
      {state && !state.ok && (
        <p className="mt-1 break-words text-xs text-red-600">{state.message}</p>
      )}
    </form>
  );
}
