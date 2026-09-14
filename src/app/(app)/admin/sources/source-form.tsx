"use client";

import Link from "next/link";
import { useActionState, useState } from "react";

import { checkSourceUrl, saveSource, type SourceActionResult } from "./actions";
import {
  CREATABLE_FETCH_METHODS,
  FETCH_METHOD_LABELS,
  LANGUAGE_SUGGESTIONS,
  REGION_SUGGESTIONS,
  SOURCE_TYPES,
  defaultFormValues,
  type SourceFormValues,
} from "./source-fields";

type Props = {
  mode: "create" | "edit";
  initial: SourceFormValues;
  /** 현재 우선순위 분포 안내 (예: "10: 1개 · 30: 5개 · 40: 8개") */
  priorityHint: string;
};

const inputClass =
  "w-full rounded border border-black/20 bg-transparent px-3 py-2 text-sm dark:border-white/25 disabled:opacity-60";
const labelClass = "block text-xs font-medium text-black/60 dark:text-white/60";
const helpClass = "mt-1 text-xs text-black/50 dark:text-white/50";
const secondaryButtonClass =
  "rounded border border-black/20 px-3 py-2 text-sm hover:bg-black/5 disabled:opacity-50 dark:border-white/25 dark:hover:bg-white/10";

function ResultMessage({ state }: { state: SourceActionResult | null }) {
  if (!state) return null;
  const color = state.ok
    ? "text-green-700 dark:text-green-400"
    : state.tone === "warn"
      ? "text-amber-700 dark:text-amber-400"
      : "text-red-600";
  return (
    <div className={`text-sm ${color}`}>
      <p>{state.message}</p>
      {state.details && state.details.length > 0 && (
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-black/60 dark:text-white/60">
          {state.details.map((d, i) => (
            <li key={i} className="break-all">
              {d}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * 수집원 추가·편집 폼 — 수집 방식에 따라 입력란이 바뀐다.
 *
 * 새로 만들 수 있는 방식은 RSS 피드·목록 페이지뿐이다 (사용자 요구 1-3).
 * 네이버·구글 뉴스 검색어는 /admin/search-keywords에서 검색어로 추가한다.
 */
export function SourceForm({ mode, initial, priorityHint }: Props) {
  const [values, setValues] = useState<SourceFormValues>(initial);
  const [saveState, saveAction, saving] = useActionState(saveSource, null);
  const [checkState, checkAction, checking] = useActionState(checkSourceUrl, null);

  // 추가에 성공하면 빈 폼으로 돌린다 (렌더 중 상태 갱신 패턴 — 효과 없이 처리)
  const [handledSave, setHandledSave] = useState<SourceActionResult | null>(null);
  if (saveState !== handledSave) {
    setHandledSave(saveState);
    if (mode === "create" && saveState?.ok) setValues(defaultFormValues());
  }

  const set =
    <K extends keyof SourceFormValues>(key: K) =>
    (
      e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>,
    ) => {
      const target = e.target;
      const next: SourceFormValues[K] = (
        target instanceof HTMLInputElement && target.type === "checkbox"
          ? target.checked
          : target.value
      ) as SourceFormValues[K];
      setValues((prev) => ({ ...prev, [key]: next }));
    };

  const onUrlChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const url = e.target.value;
    setValues((prev) => ({
      ...prev,
      url,
      // 구글 뉴스 RSS는 링크 해독이 필수 — 주소를 보고 자동으로 켠다
      link_decoder: prev.link_decoder || /news\.google\.com/i.test(url),
    }));
  };

  const method = values.fetch_method;
  const canCheck = method === "RSS" || method === "LIST_PAGE";
  const busy = saving || checking;

  return (
    <form action={saveAction} className="space-y-4">
      {mode === "edit" && <input type="hidden" name="id" value={values.id} />}
      {/* 편집 때는 select가 비활성이라 값이 안 실리므로 hidden으로 같이 보낸다 */}
      {mode === "edit" && (
        <input type="hidden" name="fetch_method" value={values.fetch_method} />
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <label className={labelClass} htmlFor="src-name">
            이름 <span className="text-red-600">*</span>
          </label>
          <input
            id="src-name"
            name="name"
            required
            maxLength={120}
            value={values.name}
            onChange={set("name")}
            placeholder="예: 로봇신문 RSS"
            className={inputClass}
          />
          <p className={helpClass}>표와 기사 출처에 표시되는 이름입니다. 중복될 수 없습니다.</p>
        </div>

        <div>
          <label className={labelClass} htmlFor="src-type">
            유형 <span className="text-red-600">*</span>
          </label>
          <select
            id="src-type"
            name="source_type"
            required
            value={values.source_type}
            onChange={set("source_type")}
            className={inputClass}
          >
            <option value="">선택하세요</option>
            {SOURCE_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <p className={helpClass}>
            같은 기사 묶음의 대표 출처와 분석 순서는 이 유형으로 정해집니다
            (정부·공공기관·정책 공고 → 기업 공식 발표·연구기관 → 전문매체 → 일반 언론).
          </p>
        </div>

        <div>
          <label className={labelClass} htmlFor="src-method">
            수집 방식 <span className="text-red-600">*</span>
          </label>
          <select
            id="src-method"
            name={mode === "create" ? "fetch_method" : undefined}
            value={values.fetch_method}
            onChange={set("fetch_method")}
            disabled={mode === "edit"}
            className={inputClass}
          >
            {(mode === "edit" && !CREATABLE_FETCH_METHODS.includes(values.fetch_method)
              ? [values.fetch_method]
              : CREATABLE_FETCH_METHODS
            ).map((m) => (
              <option key={m} value={m}>
                {FETCH_METHOD_LABELS[m]}
              </option>
            ))}
          </select>
          <p className={helpClass}>
            {mode === "edit" ? (
              "수집 방식은 바꿀 수 없습니다. 방식을 바꾸려면 새 수집원을 추가하고 이 수집원은 삭제(또는 비활성화)해 주세요."
            ) : (
              <>
                RSS 피드(주소) 또는 목록 페이지(선택자) 중 하나.{" "}
                <Link href="/admin/search-keywords" className="underline">
                  네이버·구글 뉴스 검색어는 [검색 키워드] 화면
                </Link>
                에서 추가합니다.
              </>
            )}
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={labelClass} htmlFor="src-priority">
              우선순위
            </label>
            <input
              id="src-priority"
              name="priority"
              type="number"
              min={1}
              max={100}
              value={values.priority}
              onChange={set("priority")}
              className={inputClass}
            />
            <p className={helpClass}>
              수집 배치가 수집원을 처리할 순서를 정할 때 쓰는 보조값입니다(낮을수록 먼저).
              대표 출처 선정에는 영향이 없습니다. 현재 분포 — {priorityHint}
            </p>
          </div>
          <div>
            <label className={labelClass} htmlFor="src-interval">
              수집 간격(분)
            </label>
            <input
              id="src-interval"
              name="fetch_interval_minutes"
              type="number"
              min={10}
              max={1440}
              value={values.fetch_interval_minutes}
              onChange={set("fetch_interval_minutes")}
              className={inputClass}
            />
            <p className={helpClass}>
              정기 수집 배치(하루 3회, 약 8시간 간격)가 돌 때, 마지막 성공에서 이 시간이
              지났으면 다시 가져옵니다. 배치 간격보다 짧은 값은 모두 ‘매 배치마다’와 같습니다.
              기본 100분.
            </p>
          </div>
        </div>
      </div>

      {/* 수집 방식별 입력란 */}
      <div className="rounded-lg border border-black/10 p-4 dark:border-white/15">
        {method === "RSS" && (
          <div className="space-y-3">
            <div>
              <label className={labelClass} htmlFor="src-url">
                RSS 주소 <span className="text-red-600">*</span>
              </label>
              <input
                id="src-url"
                name="url"
                type="url"
                required
                value={values.url}
                onChange={onUrlChange}
                placeholder="https://example.com/rss"
                className={inputClass}
              />
              <p className={helpClass}>
                http(s)로 시작하는 공개 주소만 됩니다. 내부망 주소·사설 IP는 등록할 수 없습니다.
              </p>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                name="link_decoder"
                checked={values.link_decoder}
                onChange={set("link_decoder")}
              />
              구글 뉴스 RSS (링크 해독 필요)
            </label>
            <p className={helpClass}>
              news.google.com 주소면 자동으로 켜집니다. 구글 뉴스 링크는 해독해야 언론사 원문 본문을 가져올 수 있습니다.
            </p>
          </div>
        )}

        {method === "NAVER_API" && (
          <div className="space-y-2">
            <p className="text-sm">
              이 수집원은 네이버 뉴스 검색어입니다. 검색어·조회 건수는{" "}
              <Link href="/admin/search-keywords" className="underline">
                [검색 키워드] 화면
              </Link>
              에서 바꿉니다. 여기서는 이름·유형·우선순위·수집 간격만 고칠 수
              있습니다.
            </p>
          </div>
        )}

        {method === "LIST_PAGE" && (
          <div className="space-y-3">
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label className={labelClass} htmlFor="src-url">
                  목록 페이지 주소 <span className="text-red-600">*</span>
                </label>
                <input
                  id="src-url"
                  name="url"
                  type="url"
                  required
                  value={values.url}
                  onChange={onUrlChange}
                  placeholder="https://www.korea.kr/news/policyNewsList.do"
                  className={inputClass}
                />
              </div>
              <div>
                <label className={labelClass} htmlFor="src-base-url">
                  기준 URL (base_url)
                </label>
                <input
                  id="src-base-url"
                  name="base_url"
                  type="url"
                  value={values.base_url}
                  onChange={set("base_url")}
                  placeholder="https://www.korea.kr (링크가 /로 시작할 때 앞에 붙일 주소)"
                  className={inputClass}
                />
                <p className={helpClass}>비우면 목록 페이지 주소를 기준으로 삼습니다.</p>
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-3">
              <div>
                <label className={labelClass} htmlFor="src-item-selector">
                  항목 선택자 (item_selector) <span className="text-red-600">*</span>
                </label>
                <input
                  id="src-item-selector"
                  name="item_selector"
                  required
                  value={values.item_selector}
                  onChange={set("item_selector")}
                  placeholder="li > a[href*='policyNewsView.do']"
                  className={`${inputClass} font-mono`}
                />
                <p className={helpClass}>기사 하나하나에 해당하는 요소. 링크(a)가 아니면 그 안의 첫 링크를 씁니다.</p>
              </div>
              <div>
                <label className={labelClass} htmlFor="src-title-selector">
                  제목 선택자 (title_selector)
                </label>
                <input
                  id="src-title-selector"
                  name="title_selector"
                  value={values.title_selector}
                  onChange={set("title_selector")}
                  placeholder="strong"
                  className={`${inputClass} font-mono`}
                />
                <p className={helpClass}>항목 안에서 제목을 찾을 위치. 비우면 항목의 글자 전체.</p>
              </div>
              <div>
                <label className={labelClass} htmlFor="src-summary-selector">
                  요약 선택자 (summary_selector)
                </label>
                <input
                  id="src-summary-selector"
                  name="summary_selector"
                  value={values.summary_selector}
                  onChange={set("summary_selector")}
                  placeholder="span.lead"
                  className={`${inputClass} font-mono`}
                />
                <p className={helpClass}>항목 안에서 요약문을 찾을 위치. 없으면 비워 두세요.</p>
              </div>
            </div>
          </div>
        )}

        {method === "PREDEFINED" && (
          <div>
            <label className={labelClass}>주소</label>
            <input name="url" value={values.url} readOnly className={inputClass} />
            <p className={helpClass}>
              코드로 만든 어댑터입니다. 주소와 설정은 화면에서 바꿀 수 없고 이름·유형·우선순위 등만 수정됩니다.
            </p>
          </div>
        )}

        {/* URL 확인 */}
        <div className="mt-3 flex flex-wrap items-center gap-3">
          {canCheck ? (
            <button
              type="submit"
              formAction={checkAction}
              formNoValidate
              disabled={busy}
              className={secondaryButtonClass}
            >
              {checking ? "확인 중…" : "URL 확인"}
            </button>
          ) : method === "NAVER_API" ? (
            <span className="text-xs text-black/50 dark:text-white/50">
              네이버 검색은 API 키가 이 서버에 없어 미리 확인할 수 없습니다. 수집 결과는 표의 ‘마지막 성공’ 시각으로 확인해 주세요.
            </span>
          ) : null}
          {canCheck && (
            <span className="text-xs text-black/50 dark:text-white/50">
              저장 전에 주소가 열리는지, RSS 항목·선택자 매칭이 있는지 대략 확인합니다 (10초·3MB까지).
            </span>
          )}
        </div>
        {checkState && (
          <div className="mt-2">
            <ResultMessage state={checkState} />
          </div>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={labelClass} htmlFor="src-region">
              지역
            </label>
            <input
              id="src-region"
              name="country_region"
              list="src-region-options"
              maxLength={40}
              value={values.country_region}
              onChange={set("country_region")}
              className={inputClass}
            />
            <datalist id="src-region-options">
              {REGION_SUGGESTIONS.map((r) => (
                <option key={r} value={r} />
              ))}
            </datalist>
          </div>
          <div>
            <label className={labelClass} htmlFor="src-language">
              언어
            </label>
            <input
              id="src-language"
              name="language"
              list="src-language-options"
              maxLength={20}
              value={values.language}
              onChange={set("language")}
              className={inputClass}
            />
            <datalist id="src-language-options">
              {LANGUAGE_SUGGESTIONS.map((l) => (
                <option key={l} value={l} />
              ))}
            </datalist>
            <p className={helpClass}>ko(한국어) · en(영어) 등 코드로.</p>
          </div>
        </div>
        <div>
          <label className={labelClass} htmlFor="src-notes">
            메모
          </label>
          <textarea
            id="src-notes"
            name="notes"
            rows={2}
            maxLength={500}
            value={values.notes}
            onChange={set("notes")}
            placeholder="왜 추가했는지, 특이사항 등 (운영자용)"
            className={inputClass}
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={busy}
          className="rounded bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
        >
          {saving ? "저장 중…" : mode === "create" ? "추가" : "저장"}
        </button>
        <Link href="/admin/sources" className={secondaryButtonClass}>
          {mode === "create" ? "닫기" : "편집 취소"}
        </Link>
        <ResultMessage state={saveState} />
      </div>
    </form>
  );
}
