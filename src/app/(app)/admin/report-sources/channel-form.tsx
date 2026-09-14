"use client";

import Link from "next/link";
import { useActionState, useId, useState } from "react";

import {
  createReportChannel,
  deleteReportChannel,
  resetReportChannelRun,
  toggleReportChannel,
  updateReportChannel,
  type ReportChannelRow,
  type ReportSourceFormOptions,
} from "./actions";
import {
  ActionMessage,
  BUTTON_PRIMARY_CLASS,
  BUTTON_SMALL_CLASS,
  Field,
  INPUT_CLASS,
} from "./source-form";

/** 자료 종류가 속한 수집원 요약 (행·폼이 공통으로 쓰는 만큼만). */
export type ChannelSourceInfo = {
  id: string;
  name: string;
  sourceKey: string;
  status: string;
  adapterKey: string | null;
};

/**
 * (자료 종류 × 검색어) 조합 진행 상황 — 서버(page.tsx)에서
 * report_channel_keyword_runs 를 집계해 내려준다.
 */
export type ChannelCombos = {
  /** 이 자료 종류가 도는 조합 수 = 검색어 개수(검색어 미사용이면 1). */
  total: number;
  /** 최근 24시간 안에 실행된 조합 수. */
  ran24h: number;
  /** 아직 한 번도 안 돌았거나 수집 주기가 지난 조합 수. */
  due: number;
  /** 조합 실행에서 마지막으로 남은 오류 (없으면 null). */
  lastError: string | null;
};

// 비밀값 취급 안내 — 화면에 고정 표시 (사용자 요구 2번 문구 그대로)
const CREDENTIAL_KEY_NOTICE =
  "여기에는 키 '이름'만 적습니다. 실제 키 값(인증키·클라이언트ID·MAC 주소)은 " +
  "GitHub 저장소 Settings → Secrets and variables → Actions 에 같은 이름으로 등록하고, " +
  ".github/workflows/collect-reports.yml의 env 목록에도 그 이름이 있어야 배치가 읽습니다. " +
  "화면·DB에는 절대 저장하지 마세요.";

/** 키 이름 안내 상자 — 관리 구역 상단과 자료 종류 폼 안에 항상 보인다. */
export function CredentialKeyNotice({ compact = false }: { compact?: boolean }) {
  return (
    <div
      className={`rounded-lg border border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200 ${
        compact ? "p-2 text-xs" : "p-3 text-sm"
      }`}
    >
      <span className="font-semibold">API 키 이름 안내 · </span>
      {CREDENTIAL_KEY_NOTICE}
    </div>
  );
}

/** 검색어는 이 화면에서 고치지 않는다 — 공용 목록으로 보낸다. */
function KeywordSourceHint({ keywordTerms }: { keywordTerms: string[] }) {
  return (
    <p className="text-xs text-black/55 dark:text-white/55">
      검색어는 여기서 정하지 않습니다. 지금 보고서 수집에 쓰는 검색어{" "}
      {keywordTerms.length}개
      {keywordTerms.length > 0 && <>: {keywordTerms.join(" · ")}</>} —{" "}
      <Link href="/admin/search-keywords" className="underline underline-offset-2">
        검색 키워드 화면
      </Link>
      에서 한 번만 정하면 모든 보고서 수집원에 똑같이 적용됩니다.
    </p>
  );
}

/**
 * 자료 종류 추가·편집 폼 본체. 어댑터 선택에 따라 config_json 예시·필요 키 이름
 * 안내가 바뀐다 (자료 종류에 어댑터가 없으면 수집원의 어댑터를 따른다).
 *
 * 검색어 입력은 없다 — 자료 종류는 '수집원 안의 자료 종류'만 정하고, 무엇을 찾을지는
 * 공용 검색어 목록이 정한다.
 */
export function ChannelForm({
  mode,
  source,
  initial,
  options,
  keywordTerms,
}: {
  mode: "create" | "edit";
  source: ChannelSourceInfo;
  initial?: ReportChannelRow;
  options: ReportSourceFormOptions;
  keywordTerms: string[];
}) {
  const isEdit = mode === "edit";
  const [state, formAction, pending] = useActionState(
    isEdit ? updateReportChannel : createReportChannel,
    null,
  );
  const [adapterKey, setAdapterKey] = useState(initial?.adapter_key ?? "");
  const sourceInfo = source.adapterKey ? options.adapterInfo[source.adapterKey] : undefined;
  const [credName, setCredName] = useState(
    initial?.credential_key_name ?? (isEdit ? "" : (sourceInfo?.credentialKeyName ?? "")),
  );
  const [usesKeywords, setUsesKeywords] = useState(initial?.uses_keywords ?? true);
  const envListId = useId();

  const effectiveAdapter = adapterKey || source.adapterKey || "";
  const info = effectiveAdapter ? options.adapterInfo[effectiveAdapter] : undefined;
  // 인프라 비밀(DB·AI·중계)은 저장 자체가 거부된다 — 누르기 전에 알려준다
  const credBlocked = credName !== "" && options.blockedEnvNames.includes(credName);
  const credMissingInWorkflow =
    credName !== "" && !credBlocked && !options.workflowEnvNames.includes(credName);

  return (
    <form
      action={formAction}
      className="basis-full space-y-3 rounded-lg border border-black/10 p-4 dark:border-white/15"
    >
      {isEdit && initial ? (
        <input type="hidden" name="id" value={initial.id} />
      ) : (
        <input type="hidden" name="source_id" value={source.id} />
      )}
      <div className="text-xs text-black/50 dark:text-white/50">
        수집원: <span className="font-medium">{source.name}</span> ({source.sourceKey})
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Field
          label="자료 종류 키 (영문 소문자 · 만든 뒤 바꾸지 마세요)"
          hint="같은 수집원 안에서 겹치면 안 됩니다. 예: web, seminar, chamgo"
        >
          <input
            name="channel_key"
            required
            pattern="[a-z0-9][a-z0-9_\-]{0,59}"
            defaultValue={initial?.channel_key ?? ""}
            placeholder="web"
            className={INPUT_CLASS}
          />
        </Field>
        <Field label="자료 종류 이름" hint="운영자가 알아볼 이름. 예: 웹자료, 세미나자료">
          <input
            name="name"
            required
            maxLength={120}
            defaultValue={initial?.name ?? ""}
            placeholder="예: 웹자료"
            className={INPUT_CLASS}
          />
        </Field>
        <Field
          label="검색어 사용"
          hint={
            usesKeywords
              ? `검색 키워드 화면의 검색어 ${keywordTerms.length}개로 각각 한 번씩 찾습니다.`
              : "검색어를 넣지 않고 목록 전체를 가져온 뒤 제목으로 걸러냅니다 (PRISM 방식)."
          }
        >
          <select
            name="uses_keywords"
            value={usesKeywords ? "true" : "false"}
            onChange={(e) => setUsesKeywords(e.target.value === "true")}
            className={INPUT_CLASS}
          >
            <option value="true">검색어를 사용해 찾습니다</option>
            <option value="false">검색어 없이 전체를 봅니다</option>
          </select>
        </Field>
        <Field label="어댑터 (수집 코드)" hint="비우면 수집원의 어댑터를 따릅니다.">
          <select
            name="adapter_key"
            value={adapterKey}
            onChange={(e) => {
              const next = e.target.value;
              setAdapterKey(next);
              // 새 자료 종류면 어댑터에 맞는 기본 키 이름을 채워 준다 (편집은 손대지 않음)
              if (!isEdit) {
                const nextInfo = options.adapterInfo[next || source.adapterKey || ""];
                setCredName(nextInfo?.credentialKeyName ?? "");
              }
            }}
            className={INPUT_CLASS}
          >
            <option value="">
              수집원 설정 따름 ({source.adapterKey ?? "없음"})
            </option>
            {options.adapterKeys.map((k) => (
              <option key={k} value={k}>
                {options.adapterInfo[k]?.label ?? k}
              </option>
            ))}
          </select>
        </Field>
        <Field label="수집 방식 (기록용)" hint="어떤 방법으로 가져오는지 적어 두는 칸입니다.">
          <select
            name="collection_method"
            defaultValue={initial?.collection_method ?? "API"}
            className={INPUT_CLASS}
          >
            {options.collectionMethods.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="다시 찾기까지 기다리는 시간 (시간)"
          hint="검색어 하나를 찾은 뒤 이 시간이 지나야 같은 검색어를 다시 찾습니다 (기본 24)"
        >
          <input
            name="fetch_interval_hours"
            type="number"
            min={1}
            max={720}
            defaultValue={initial?.fetch_interval_hours ?? 24}
            className={INPUT_CLASS}
          />
        </Field>
        <Field label="한 번에 볼 페이지 수" hint="검색어 하나당 넘겨 볼 목록 페이지 수 (기본 3)">
          <input
            name="max_pages_per_run"
            type="number"
            min={1}
            max={100}
            defaultValue={initial?.max_pages_per_run ?? 3}
            className={INPUT_CLASS}
          />
        </Field>
        <Field label="한 번에 가져올 최대 건수" hint="검색어 하나당 가져올 자료 수 상한 (기본 200)">
          <input
            name="max_items_per_run"
            type="number"
            min={1}
            max={2000}
            defaultValue={initial?.max_items_per_run ?? 200}
            className={INPUT_CLASS}
          />
        </Field>
        <Field label="요청 사이 쉬는 시간 (ms)" hint="상대 서버를 배려하는 대기 시간 (기본 1500 = 1.5초)">
          <input
            name="request_interval_ms"
            type="number"
            min={0}
            max={60000}
            defaultValue={initial?.request_interval_ms ?? 1500}
            className={INPUT_CLASS}
          />
        </Field>
      </div>

      <KeywordSourceHint keywordTerms={keywordTerms} />

      {info?.note && (
        <p className="text-xs text-black/55 dark:text-white/55">
          {effectiveAdapter} 어댑터: {info.note}
        </p>
      )}

      <Field
        label="하위 구분 · 추가 설정 (JSON, 선택)"
        hint={
          <>
            {info
              ? `${effectiveAdapter} 어댑터가 읽는 값 — ${info.configHelp}`
              : "어댑터를 먼저 고르면 예시가 보입니다."}
            {" / "}공통: has_viewer: true — 접근성 검사에서 &lsquo;온라인 뷰어 제공&rsquo;으로 간주.
            {" "}큰따옴표(&quot;)를 쓰고 {"{ }"}로 감싼 한 덩어리여야 합니다. 같은 수집원의
            자료 종류를 나누는 값(예: 국회도서관 dbname, POINT category)이 여기 들어갑니다.
          </>
        }
      >
        <textarea
          name="config_json"
          rows={3}
          defaultValue={initial?.config_json ? JSON.stringify(initial.config_json, null, 2) : ""}
          placeholder={info?.configExample ?? '{"year_from": 2021}'}
          spellCheck={false}
          className={`${INPUT_CLASS} font-mono`}
        />
      </Field>

      <div className="space-y-2">
        <Field
          label="필요한 키 이름 (env 변수 이름, 선택)"
          hint={
            <>
              {info?.credentialKeyName
                ? `${effectiveAdapter} 어댑터 기본: ${info.credentialKeyName}`
                : info
                  ? `${effectiveAdapter} 어댑터는 키가 필요 없습니다.`
                  : "어댑터별 기본값이 있습니다."}
              {info && info.extraEnvNames.length > 0 && (
                <> · 워크플로 env로만 주입되는 추가 키: {info.extraEnvNames.join(", ")}</>
              )}
            </>
          }
        >
          <input
            name="credential_key_name"
            list={envListId}
            value={credName}
            onChange={(e) => setCredName(e.target.value.toUpperCase())}
            pattern="[A-Z][A-Z0-9_]{1,63}"
            placeholder="예: NKIS_API_KEY"
            autoComplete="off"
            className={`${INPUT_CLASS} font-mono`}
          />
          <datalist id={envListId}>
            {options.workflowEnvNames.map((n) => (
              <option key={n} value={n} />
            ))}
          </datalist>
        </Field>
        {credBlocked && (
          <p className="text-xs font-medium text-red-600 dark:text-red-400">
            &lsquo;{credName}&rsquo;은(는) 데이터베이스·AI·중계용 비밀이라 자료 종류의 키 이름으로 쓸 수 없어
            저장이 거부됩니다. 배치가 이 값을 그대로 외부 사이트에 보내기 때문입니다 — 어댑터용 키
            이름(예: NKIS_API_KEY)을 넣으세요.
          </p>
        )}
        {credMissingInWorkflow && (
          <p className="text-xs text-red-600 dark:text-red-400">
            &lsquo;{credName}&rsquo;은(는) 현재 collect-reports.yml env 목록에 없습니다. 저장은 되지만
            GitHub Secrets 등록 + 워크플로 env 추가 전에는 배치가 이 자료 종류를 &lsquo;자격증명 없음&rsquo;으로
            실패 처리합니다.
          </p>
        )}
        <CredentialKeyNotice compact />
      </div>

      <div className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" name="enabled" defaultChecked={initial?.enabled ?? true} />
          활성 (배치가 실행)
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" name="supports_abstract" defaultChecked={initial?.supports_abstract ?? true} />
          초록 제공
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" name="supports_file_metadata" defaultChecked={initial?.supports_file_metadata ?? false} />
          파일 정보 제공
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" name="supports_direct_download" defaultChecked={initial?.supports_direct_download ?? false} />
          원문 직접 다운로드
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" name="supports_viewer" defaultChecked={initial?.supports_viewer ?? false} />
          온라인 뷰어
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" disabled={pending} className={BUTTON_PRIMARY_CLASS}>
          {pending ? "저장 중…" : isEdit ? "저장" : "자료 종류 추가"}
        </button>
        <ActionMessage state={state} />
      </div>
    </form>
  );
}

/**
 * '자료 종류 추가' 버튼 + 펼쳐지는 폼 (부모 flex-wrap 행 안에서 사용).
 * 평소에는 쓸 일이 없다 — 검색어를 늘릴 때는 [검색 키워드] 화면만 쓰면 된다.
 */
export function ChannelAddButton({
  source,
  options,
  keywordTerms,
}: {
  source: ChannelSourceInfo;
  options: ReportSourceFormOptions;
  keywordTerms: string[];
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen((o) => !o)} className={BUTTON_SMALL_CLASS}>
        {open ? "추가 취소" : "자료 종류 추가"}
      </button>
      {!open && (
        <span className="self-center text-xs text-black/45 dark:text-white/45">
          검색어를 늘릴 때는 필요 없습니다 — 같은 사이트에서 자료 종류(예: 국회도서관
          &lsquo;학위논문&rsquo;)를 따로 나눌 때만 쓰세요.
        </span>
      )}
      {open && (
        <ChannelForm mode="create" source={source} options={options} keywordTerms={keywordTerms} />
      )}
    </>
  );
}

/**
 * 자료 종류 표의 한 행 (+ 펼치면 편집 행). 서버가 만든 <tbody> 안에 <tr>를
 * 돌려주므로 시각 표시는 서버에서 문자열로 만들어 넘긴다 (hydration 불일치 방지).
 */
export function ChannelRow({
  channel,
  source,
  options,
  keywordTerms,
  combos,
  lastRunLabel,
  lastSuccessLabel,
}: {
  channel: ReportChannelRow;
  source: ChannelSourceInfo;
  options: ReportSourceFormOptions;
  keywordTerms: string[];
  combos: ChannelCombos;
  lastRunLabel: string;
  lastSuccessLabel: string;
}) {
  const [editing, setEditing] = useState(false);
  const [resetState, resetAction, resetPending] = useActionState(resetReportChannelRun, null);
  const [deleteState, deleteAction, deletePending] = useActionState(deleteReportChannel, null);

  const effectiveAdapter = channel.adapter_key ?? source.adapterKey;
  const info = effectiveAdapter ? options.adapterInfo[effectiveAdapter] : undefined;
  const credBlocked =
    !!channel.credential_key_name && options.blockedEnvNames.includes(channel.credential_key_name);
  const credMissing =
    !!channel.credential_key_name &&
    !credBlocked &&
    !options.workflowEnvNames.includes(channel.credential_key_name);
  const configSummary = channel.config_json
    ? Object.entries(channel.config_json)
        .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
        .join(", ")
    : "";
  const error = channel.last_error ?? combos.lastError;
  // 조합이 하나라도 주기가 지났고, 자료 종류·수집원이 모두 켜져 있으면 다음 수집 대상
  const willRunNext = combos.due > 0 && channel.enabled && source.status === "ACTIVE";
  // 조합이 0개면(검색어 목록이 비었거나 불러오지 못함) '이미 다 대상'이 아니라
  // 실행할 것 자체가 없는 상태다 — 둘을 구분해서 안내한다
  const noCombos = combos.total === 0;
  const allDue = !noCombos && combos.due >= combos.total;

  return (
    <>
      <tr className="border-b border-black/5 align-top last:border-0 dark:border-white/10">
        <td className="p-3">
          <div className="font-medium">{channel.name}</div>
          <div className="text-xs text-black/45 dark:text-white/45">
            {channel.channel_key} · {effectiveAdapter ?? "adapter 없음"} ·{" "}
            {channel.fetch_interval_hours}시간마다 · 한 번에 {channel.max_pages_per_run}페이지 ·
            최대 {channel.max_items_per_run}건 · {channel.request_interval_ms}ms 쉼
          </div>
          {configSummary && (
            <div
              className="mt-0.5 max-w-[240px] truncate font-mono text-xs text-black/45 dark:text-white/45"
              title={configSummary}
            >
              하위 구분: {configSummary}
            </div>
          )}
        </td>
        <td className="p-3">
          {channel.uses_keywords ? (
            <span className="text-black/70 dark:text-white/70">사용함</span>
          ) : (
            <>
              <span className="text-black/70 dark:text-white/70">사용 안 함</span>
              <div className="text-xs text-black/45 dark:text-white/45">
                검색어 없이 전체를 봅니다
              </div>
            </>
          )}
        </td>
        <td className="p-3">
          {channel.uses_keywords ? (
            <>
              검색어 {keywordTerms.length}개 × 이 자료 종류
              <div className="text-xs text-black/45 dark:text-white/45">
                조합 {combos.total}개 · 최근 24시간 {combos.ran24h}개 실행
              </div>
            </>
          ) : (
            <>
              검색어 없이 전체
              <div className="text-xs text-black/45 dark:text-white/45">
                조합 1개 · 최근 24시간 {combos.ran24h}개 실행
              </div>
            </>
          )}
          {willRunNext && (
            <div className="text-xs text-amber-600 dark:text-amber-400">
              다음 수집에서 {combos.due}개 실행 예정
            </div>
          )}
        </td>
        <td className="p-3">
          {channel.enabled ? (
            <span className="text-green-700 dark:text-green-400">활성</span>
          ) : (
            <span className="text-black/40 dark:text-white/40">비활성</span>
          )}
        </td>
        <td className="whitespace-nowrap p-3">{lastRunLabel}</td>
        <td className="whitespace-nowrap p-3">{lastSuccessLabel}</td>
        <td className="p-3">
          {error ? (
            <div className="max-w-[200px] truncate text-xs text-red-600 dark:text-red-400" title={error}>
              {error}
            </div>
          ) : (
            "—"
          )}
        </td>
        <td className="p-3 text-xs">
          {channel.credential_key_name ? (
            <code>{channel.credential_key_name}</code>
          ) : (
            <span className="text-black/40 dark:text-white/40">없음</span>
          )}
          {credBlocked && (
            <div className="font-medium text-red-600 dark:text-red-400">
              쓸 수 없는 이름 — 배치가 실패시킵니다
            </div>
          )}
          {credMissing && (
            <div className="text-red-600 dark:text-red-400">워크플로 env에 없음</div>
          )}
          {info && info.extraEnvNames.length > 0 && (
            <div className="text-black/45 dark:text-white/45">+ {info.extraEnvNames.join(", ")} (env)</div>
          )}
        </td>
        <td className="p-3">
          <div className="flex flex-wrap gap-1.5">
            <form action={toggleReportChannel}>
              <input type="hidden" name="id" value={channel.id} />
              <input type="hidden" name="enable" value={String(!channel.enabled)} />
              <button type="submit" className={BUTTON_SMALL_CLASS}>
                {channel.enabled ? "비활성화" : "활성화"}
              </button>
            </form>
            <form action={resetAction}>
              <input type="hidden" name="id" value={channel.id} />
              <button
                type="submit"
                disabled={resetPending || allDue || noCombos}
                title={
                  noCombos
                    ? "찾을 검색어가 없습니다 — [검색 키워드] 화면에서 먼저 추가해 주세요."
                    : allDue
                      ? "이미 모든 검색어가 다음 수집 대상입니다"
                      : "수집 주기와 상관없이 다음 수집에서 검색어 전부를 다시 찾습니다"
                }
                className={BUTTON_SMALL_CLASS}
              >
                {resetPending
                  ? "처리 중…"
                  : noCombos
                    ? "실행할 검색어 없음"
                    : allDue
                      ? "다음 수집 대상"
                      : "지금 실행 대상으로"}
              </button>
            </form>
            <button type="button" onClick={() => setEditing((v) => !v)} className={BUTTON_SMALL_CLASS}>
              {editing ? "닫기" : "설정"}
            </button>
            <form
              action={deleteAction}
              onSubmit={(e) => {
                if (
                  !window.confirm(
                    `자료 종류 '${channel.name}'을(를) 더 이상 수집하지 않을까요?\n\n` +
                      "이미 모은 자료가 있으면 지우지 않고 화면 아래 '검색 키워드로 통합된 옛 채널'로 옮깁니다.",
                  )
                ) {
                  e.preventDefault();
                }
              }}
            >
              <input type="hidden" name="id" value={channel.id} />
              <button
                type="submit"
                disabled={deletePending}
                className={`${BUTTON_SMALL_CLASS} text-red-600 dark:text-red-400`}
              >
                {deletePending ? "정리 중…" : "수집 중단"}
              </button>
            </form>
          </div>
          {(resetState || deleteState) && (
            <div className="mt-1 max-w-[260px] text-xs">
              {resetState && (
                <p className={resetState.ok ? "text-green-700 dark:text-green-400" : "text-red-600"}>
                  {resetState.message}
                </p>
              )}
              {deleteState && (
                <p className={deleteState.ok ? "text-green-700 dark:text-green-400" : "text-red-600"}>
                  {deleteState.message}
                </p>
              )}
            </div>
          )}
        </td>
      </tr>
      {editing && (
        <tr className="border-b border-black/5 last:border-0 dark:border-white/10">
          <td colSpan={9} className="bg-black/[0.02] p-3 dark:bg-white/[0.03]">
            <ChannelForm
              mode="edit"
              source={source}
              initial={channel}
              options={options}
              keywordTerms={keywordTerms}
            />
          </td>
        </tr>
      )}
    </>
  );
}
