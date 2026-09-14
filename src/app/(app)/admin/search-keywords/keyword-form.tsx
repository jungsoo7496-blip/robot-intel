"use client";

import { useActionState, useState } from "react";

import {
  addSearchKeyword,
  deleteSearchKeyword,
  type KeywordActionState,
} from "./actions";
import {
  ALT_TERM_MAX_LENGTH,
  MAX_ALT_TERMS,
  NOTE_MAX_LENGTH,
  SCOPE_OPTIONS,
  TERM_MAX_LENGTH,
  googleNewsRssUrl,
  googleQuery,
  normalizeAltTerms,
  scopeUsesNews,
  scopeUsesReports,
  type KeywordScope,
} from "./keyword-fields";

/*
 * 검색 키워드 화면의 클라이언트 조각 (요구 1-2·2-1·2-2).
 * - KeywordForm: 검색어 추가 + 만들어질 구글 주소 미리보기
 * - DeleteKeywordButton: 검색어 삭제 (이미 모은 기사 수를 확인 문구에 넣는다)
 */

const inputClass =
  "w-full rounded border border-black/20 bg-transparent px-3 py-2 text-sm dark:border-white/25";
const labelClass = "block text-xs font-medium text-black/60 dark:text-white/60";
const helpClass = "mt-1 text-xs text-black/50 dark:text-white/50";
const primaryButtonClass =
  "rounded bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50";

function StateMessage({ state }: { state: KeywordActionState }) {
  if (!state) return null;
  return (
    <p
      aria-live="polite"
      className={`text-sm ${
        state.ok ? "text-green-700 dark:text-green-400" : "text-red-600"
      }`}
    >
      {state.message}
    </p>
  );
}

/**
 * 검색어 추가 폼.
 *
 * 미리보기는 화면 전용이다 — 실제 수집 주소를 만드는 것은 수집 배치뿐이고,
 * 여기서는 "이 검색어를 넣으면 어떤 주소가 만들어지는지"만 같은 규칙으로
 * 계산해 보여준다 (keyword-fields.ts의 googleNewsRssUrl 주석 참고).
 */
export function KeywordForm({ disabled = false }: { disabled?: boolean }) {
  const [state, formAction, pending] = useActionState(addSearchKeyword, null);
  const [term, setTerm] = useState("");
  const [altRaw, setAltRaw] = useState("");
  const [scope, setScope] = useState<KeywordScope>("ALL");
  const [note, setNote] = useState("");

  // 추가에 성공하면 입력을 비운다 (렌더 중 상태 갱신 — 효과 없이 처리)
  const [handled, setHandled] = useState<KeywordActionState>(null);
  if (state !== handled) {
    setHandled(state);
    if (state?.ok) {
      setTerm("");
      setAltRaw("");
      setNote("");
      setScope("ALL");
    }
  }

  const alts = normalizeAltTerms(altRaw);
  const trimmed = term.trim();
  const scopeHelp = SCOPE_OPTIONS.find((o) => o.value === scope)?.help ?? "";

  return (
    <form action={formAction} className="space-y-3">
      <div className="grid gap-3 md:grid-cols-[1fr_1fr_200px]">
        <div>
          <label className={labelClass} htmlFor="kw-term">
            검색어 <span className="text-red-600">*</span>
          </label>
          <input
            id="kw-term"
            name="term"
            required
            maxLength={TERM_MAX_LENGTH}
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder="예: 물류로봇"
            className={inputClass}
          />
          <p className={helpClass}>
            네이버 뉴스와 보고서 수집원에는 이 말이 그대로 들어갑니다.
          </p>
        </div>
        <div>
          <label className={labelClass} htmlFor="kw-alt">
            같은 뜻 다른 표기 (선택)
          </label>
          <input
            id="kw-alt"
            name="alt_terms"
            maxLength={500}
            value={altRaw}
            onChange={(e) => setAltRaw(e.target.value)}
            placeholder="쉼표로 구분 — 예: Physical AI, Embodied AI"
            className={inputClass}
          />
          <p className={helpClass}>
            구글에서만 함께 찾습니다({MAX_ALT_TERMS}개까지, 하나에{" "}
            {ALT_TERM_MAX_LENGTH}자 이내).
          </p>
        </div>
        <div>
          <label className={labelClass} htmlFor="kw-scope">
            쓰는 곳
          </label>
          <select
            id="kw-scope"
            name="scope"
            value={scope}
            onChange={(e) => setScope(e.target.value as KeywordScope)}
            className={inputClass}
          >
            {SCOPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <p className={helpClass}>{scopeHelp}</p>
        </div>
      </div>

      <div>
        <label className={labelClass} htmlFor="kw-note">
          메모 (선택)
        </label>
        <input
          id="kw-note"
          name="note"
          maxLength={NOTE_MAX_LENGTH}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="왜 넣었는지 — 운영자용 메모"
          className={inputClass}
        />
      </div>

      {/* 요구 1-2 확인용 미리보기 — 검색어에서 주소가 자동으로 만들어진다 */}
      <div className="rounded-lg border border-black/10 bg-black/[0.02] p-3 text-xs dark:border-white/15 dark:bg-white/[0.04]">
        <div className="font-medium text-black/70 dark:text-white/70">
          이 검색어로 만들어질 것
        </div>
        {!trimmed ? (
          <p className="mt-1 text-black/50 dark:text-white/50">
            검색어를 입력하면 여기에 미리 보여 드립니다.
          </p>
        ) : (
          <ul className="mt-1 space-y-1 text-black/60 dark:text-white/60">
            {scopeUsesNews(scope) ? (
              <>
                <li>
                  <span className="font-medium">네이버 뉴스 검색어</span>{" "}
                  <span className="font-mono break-all">{trimmed}</span>
                </li>
                <li>
                  <span className="font-medium">구글 검색어</span>{" "}
                  <span className="font-mono break-all">
                    {googleQuery(trimmed, alts)}
                  </span>
                </li>
                <li>
                  <span className="font-medium">구글 뉴스 RSS 주소</span>{" "}
                  <span className="font-mono break-all">
                    {googleNewsRssUrl(trimmed, alts)}
                  </span>
                </li>
              </>
            ) : (
              <li>뉴스에는 쓰지 않습니다 — 구글 주소는 만들어지지 않습니다.</li>
            )}
            <li>
              {scopeUsesReports(scope)
                ? "보고서 수집원에서도 이 검색어로 찾습니다 (‘정책연구 과제’ 한 곳만 검색어 없이 전체를 봅니다)."
                : "보고서 수집원에서는 쓰지 않습니다."}
            </li>
          </ul>
        )}
        <p className="mt-2 text-black/40 dark:text-white/40">
          미리보기입니다 — 실제 주소는 다음 수집 때 만들어집니다.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={pending || disabled}
          className={primaryButtonClass}
        >
          {pending ? "추가 중…" : "검색어 추가"}
        </button>
        <StateMessage state={state} />
      </div>
    </form>
  );
}

/**
 * 검색어 삭제 버튼.
 *
 * 확인 문구에 이미 모은 기사 수를 넣는다 — 지워도 기사가 남는다는 것을
 * 숫자로 보여줘야 운영자가 안심하고 정리할 수 있다.
 */
export function DeleteKeywordButton({
  id,
  term,
  collectedCount,
}: {
  id: string;
  term: string;
  /** 이 검색어로 지금까지 모은 기사 수 (raw_items). */
  collectedCount: number;
}) {
  const [state, formAction, pending] = useActionState(deleteSearchKeyword, null);
  const countText = collectedCount.toLocaleString("ko-KR");

  return (
    <form
      action={formAction}
      onSubmit={(e) => {
        const text =
          collectedCount > 0
            ? `‘${term}’으로 모은 기사 ${countText}건은 그대로 남고, 앞으로만 수집하지 않습니다. 지울까요?`
            : `‘${term}’을(를) 목록에서 지울까요? 같은 검색어를 다시 넣으면 이어서 수집됩니다.`;
        if (!window.confirm(text)) e.preventDefault();
      }}
    >
      <input type="hidden" name="id" value={id} />
      <button
        type="submit"
        disabled={pending}
        className="rounded border border-red-300 px-2 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-950/40"
      >
        {pending ? "처리 중…" : "삭제"}
      </button>
      {state && !state.ok && (
        <p className="mt-1 max-w-[16rem] text-xs text-red-600">{state.message}</p>
      )}
    </form>
  );
}
