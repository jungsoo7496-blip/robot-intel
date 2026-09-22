"""정책·동향 보고서 수집 배치 (스펙 §12~§18).

ACTIVE 소스의 살아 있는 채널 × 검색어 조합을 순회하며:
  어댑터 fetch_recent → pre-filter → occurrence upsert → document 연결
  → (예산 내) 상세 보강·접근성 검증 → 분석 큐 적재 → run 통계 기록

검색어는 채널마다 적는 게 아니라 search_keywords 한 목록에서 온다 (2026-09-08).
실행 단위가 '채널'에서 '(채널 × 검색어) 조합'으로 바뀌어 수가 늘었으므로,
건수 상한만이 아니라 시간 예산으로도 멈춘다.

파일(PDF/HWP)은 저장하지 않는다 — Range GET 64KB로 판정만 한다.
Gemini 호출 없음 (분석은 analyze_reports 별도 배치).

실행: python -m kiro_batch.collect_reports
환경: REPORT_ACCESS_CHECK_MAX_PER_RUN(기본 50),
      REPORT_ACCESS_CHECK_MAX_PER_SOURCE(기본 15),
      REPORT_MAX_TASKS_PER_RUN(기본 40, 옛 이름 REPORT_MAX_CHANNELS_PER_RUN도 인정),
      REPORT_FETCH_BUDGET_SECONDS(기본 480)
"""

from __future__ import annotations

import os
import re
import sys
import time

import httpx

from .config import Settings
from .db import connect
from .reports import repository as repo
from .reports.access import check_candidate
from .reports.base import ChannelConfig
from .reports.classify import configure_from_db as configure_report_tiers
from .reports.classify import keyword_tier
from .reports.registry import get_adapter
from .reports.relay import relay_mounts

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; KIRO-RobotIntel/0.1)"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    return int(raw) if raw.isdigit() else default


# 어댑터는 API 키를 GET 쿼리(serviceKey= 등)에 넣고 raise_for_status()를 부른다.
# httpx가 만드는 예외 문구에는 요청 URL이 통째로 들어가므로, 그대로 두면 키가
# report_source_runs.notes · channels.last_error · sources.last_error 로 DB에 남고
# 운영 화면(보고서 수집원 관리)의 '오류' 칸에까지 그대로 보인다. 아래 두 함수가
# 저장·출력 직전에 그 값을 지운다.
_SECRET_PARAM_RE = re.compile(
    r"(?i)\b(serviceKey|token|client_id|accounts|apikey|api_key)=[^&\s'\"]*"
)

# 어댑터 자격증명으로 쓸 수 없는 env 이름 — 화면(actions.ts)의 거부 목록과 같다.
_NON_CREDENTIAL_ENV_NAMES = frozenset({
    "SUPABASE_DB_URL",
    "GEMINI_API_KEY",
    "REPORT_RELAY_URL",
    "REPORT_RELAY_TOKEN",
    "REPORT_AI_MAX_CALLS_PER_RUN",
    "REPORT_AI_DAILY_CALL_LIMIT",
    "REPORT_SKIP_SOURCES",
})


def _safe_error(e: BaseException) -> str:
    """오류 문구에서 자격증명(쿼리 파라미터 값)을 지운다 — DB·로그 저장용."""
    if isinstance(e, httpx.HTTPStatusError):
        # 상태 코드와 경로만 남기고 쿼리 전체를 버린다 (키는 늘 쿼리에 있다)
        msg = (
            f"HTTP {e.response.status_code} {e.response.reason_phrase} for "
            f"{e.request.url.copy_with(query=None)}"
        )
    else:
        msg = str(e)
    return _SECRET_PARAM_RE.sub(r"\1=***", msg)


def run_channel(
    conn,
    client: httpx.Client,
    ch: repo.ChannelTask,
    access_budget: int,
) -> tuple[dict[str, int], int]:
    """(채널 × 검색어) 조합 1개 실행. (통계, 사용한 access 예산) 반환."""
    stats = {k: 0 for k in (
        "fetched_count", "new_occurrence_count", "new_document_count",
        "duplicate_count", "prefilter_pass_count", "prefilter_excluded_count",
        "access_checked_count", "usable_count", "ai_queued_count", "error_count",
    )}

    adapter = get_adapter(ch.adapter_key)
    if adapter is None:
        raise RuntimeError(f"어댑터 미구현: {ch.adapter_key!r}")

    # 채널 설정이 인프라 비밀(DB 접속 문자열·AI 키·중계 토큰)을 가리키면 그 값이
    # 그대로 외부 API의 GET 쿼리로 나간다 — 화면에서 막지만 DB를 직접 고친 경우도
    # 있으므로 실행 직전에 한 번 더 끊는다. (값이 아니라 이름만 로그에 남는다)
    if ch.credential_key_name in _NON_CREDENTIAL_ENV_NAMES:
        raise RuntimeError(
            f"credential_key_name이 인프라 비밀({ch.credential_key_name})을 가리킵니다"
            " — 채널 설정의 '필요한 키 이름'을 어댑터용 키로 고치세요"
        )

    credential = (
        os.environ.get(ch.credential_key_name) if ch.credential_key_name else None
    )
    if ch.credential_key_name and not credential:
        raise RuntimeError(f"자격증명 없음: {ch.credential_key_name} (env 미설정)")

    config = ChannelConfig(
        source_key=ch.source_key,
        channel_key=ch.channel_key,
        query=ch.query,
        config=ch.config,
        max_pages=ch.max_pages,
        max_items=ch.max_items,
        request_interval_ms=ch.request_interval_ms,
        credential=credential,
    )

    try:
        candidates = adapter.fetch_recent(config)
        stats["fetched_count"] = len(candidates)

        access_used = 0
        for cand in candidates:
            tier = keyword_tier(cand.title, cand.abstract)
            if tier is None:
                stats["prefilter_excluded_count"] += 1
                continue
            stats["prefilter_pass_count"] += 1

            occ_id, is_new, changed = repo.upsert_occurrence(
                conn, ch.source_id, ch.channel_id, cand
            )
            if is_new:
                stats["new_occurrence_count"] += 1
            else:
                stats["duplicate_count"] += 1

            # 상세 보강 + 접근성 검증은 신규 occurrence, 예산 내에서만
            if is_new and access_used < access_budget:
                try:
                    cand = adapter.fetch_detail(cand)
                except Exception as e:  # noqa: BLE001 — 개별 실패는 채널을 죽이지 않는다
                    stats["error_count"] += 1
                    print(
                        f"  [detail 실패] {cand.external_id}: {_safe_error(e)}",
                        file=sys.stderr,
                    )
                # 상세로 얻은 초록·키워드·원문 후보를 occurrence에도 반영
                repo.upsert_occurrence(conn, ch.source_id, ch.channel_id, cand)
                result = check_candidate(
                    client,
                    cand.candidate_download_urls,
                    cand.detail_url,
                    has_viewer_hint=cand.viewer_hint
                    or bool(ch.config.get("has_viewer")),
                )
                access_used += 1
                stats["access_checked_count"] += 1
                repo.update_access(conn, occ_id, result)
                if result.status in (
                    "DIRECT_DOWNLOAD", "SOURCE_DOWNLOAD", "VIEW_ONLY",
                ):
                    stats["usable_count"] += 1

            doc_id, doc_new = repo.link_document(conn, occ_id, cand)
            if doc_new:
                stats["new_document_count"] += 1
            # TOOL(드론·AI 등 인접 영역)은 후순위 — 쿼터는 로봇 핵심부터
            job_priority = 100 if tier == "CORE" else 200
            if (is_new or changed) and repo.queue_analysis(
                conn, doc_id, cand, priority=job_priority
            ):
                stats["ai_queued_count"] += 1

            conn.commit()
    finally:
        if hasattr(adapter, "close"):
            adapter.close()

    return stats, stats["access_checked_count"]


def exit_code_for(succeeded: int, failed: int) -> int:
    """조합(채널×검색어) 성공·실패 수 → 프로세스 종료 코드.

    PRISM·ScienceON·NANET 같은 사이트 하나가 응답을 안 해도 나머지 조합은 다
    수집된다. 그런데 실패가 1개라도 있으면 1을 돌려줘 Actions 실행 전체가
    '실패'로 찍혔다 (2026-09-22 최근 5회 중 4회). 실패한 조합은 source_runs·
    채널의 마지막 오류에 그대로 남으니 프로세스는 하나라도 성공했으면 0,
    전부 실패했을 때만 1.
    """
    if failed == 0:
        return 0
    return 0 if succeeded > 0 else 1


def main() -> int:
    settings = Settings.from_env()
    settings.require("supabase_db_url")

    access_total = _env_int("REPORT_ACCESS_CHECK_MAX_PER_RUN", 50)
    access_per_source = _env_int("REPORT_ACCESS_CHECK_MAX_PER_SOURCE", 15)
    # 실행 단위가 조합(채널 × 검색어)이 되면서 상한을 40으로 올렸다.
    # 옛 이름(REPORT_MAX_CHANNELS_PER_RUN)이 설정돼 있으면 그 값을 그대로 쓴다.
    max_tasks = _env_int(
        "REPORT_MAX_TASKS_PER_RUN", _env_int("REPORT_MAX_CHANNELS_PER_RUN", 40)
    )
    # 상한만으로는 조합별 소요가 달라 job timeout(15분)을 넘길 수 있다.
    fetch_budget = _env_int("REPORT_FETCH_BUDGET_SECONDS", 480)

    # 특정 소스를 이 실행에서 건너뛴다 (2026-08-25). 용도: POINT는
    # 국립중앙도서관이 데이터센터 IP를 전부 차단해 GitHub·Vercel 어디서도
    # 안 되므로, Actions에서는 시도 자체를 생략한다 (매번 8채널 × 타임아웃
    # ~10초 = 낭비 + 실패 통계 오염). 수집은 사무실 PC 주간 예약 실행이
    # 담당한다 — 그쪽에는 이 env가 없어 POINT가 정상 실행된다.
    skip_sources = {
        s.strip() for s in os.environ.get("REPORT_SKIP_SOURCES", "").split(",")
        if s.strip()
    }

    conn = connect(settings.supabase_db_url)
    succeeded = 0
    failed: list[str] = []
    try:
        # 보고서 계층(core/tool) 키워드 — keyword_rules, 실패 시 내장 기본값
        configure_report_tiers(conn)
        tasks = repo.fetch_due_tasks(conn)
        if skip_sources:
            skipped = [t for t in tasks if t.source_key in skip_sources]
            if skipped:
                print(
                    f"[collect_reports] 건너뜀({', '.join(sorted(skip_sources))}): "
                    f"{len(skipped)}조합 — REPORT_SKIP_SOURCES"
                )
            tasks = [t for t in tasks if t.source_key not in skip_sources]
        total_due = len(tasks)
        tasks = tasks[:max_tasks]
        if not tasks:
            print("[collect_reports] 실행할 조합 없음 (due 아님 또는 비활성)")
            return 0
        if total_due > len(tasks):
            print(
                f"[collect_reports] 이번 실행 {len(tasks)}조합 "
                f"(대기 {total_due} — 나머지는 다음 실행으로)"
            )

        access_remaining = access_total
        source_access_used: dict[str, int] = {}
        deadline = time.monotonic() + fetch_budget

        # 접근성 검사(check_candidate)도 차단 호스트(POINT·ALIO)의 파일 URL을
        # 건드리므로 같은 중계 mounts를 쓴다 (미설정 시 직접 접속 — 로컬)
        with httpx.Client(
            headers=_HEADERS, timeout=15.0, follow_redirects=True,
            mounts=relay_mounts(),
        ) as client:
            for index, ch in enumerate(tasks):
                # 조합마다 소요가 달라 건수 상한만으로는 job timeout을 넘길 수
                # 있다 — 남은 조합은 다음 실행이 집는다(회전 상태가 기록돼
                # 있으므로 오래 굶은 것부터 먼저 온다).
                if time.monotonic() > deadline:
                    print(
                        f"[collect_reports] 수집 예산 소진 — 남은 "
                        f"{len(tasks) - index}개 조합은 다음 실행으로"
                    )
                    break
                label = f"{ch.source_key}/{ch.channel_key}"
                if ch.keyword_term:
                    label += f" ‘{ch.keyword_term}’"
                used_by_source = source_access_used.get(ch.source_id, 0)
                budget = max(
                    0, min(access_remaining, access_per_source - used_by_source)
                )
                try:
                    stats, used = run_channel(conn, client, ch, budget)
                    access_remaining -= used
                    source_access_used[ch.source_id] = used_by_source + used
                    repo.record_run(
                        conn, ch.source_id, ch.channel_id, "SUCCESS", stats,
                        keyword=ch.keyword_term,
                    )
                    repo.mark_task_result(
                        conn, ch.channel_id, ch.keyword_term, ok=True
                    )
                    # 채널·소스 단위 표시도 유지한다 — 보고서 화면의
                    # '마지막 성공'이 살아 있어야 한다.
                    repo.mark_channel_result(conn, ch.channel_id, ok=True)
                    repo.mark_source_result(conn, ch.source_id, ok=True)
                    conn.commit()
                    succeeded += 1
                    print(
                        f"[collect_reports] {label}: "
                        f"수집 {stats['fetched_count']} · "
                        f"필터통과 {stats['prefilter_pass_count']} · "
                        f"신규 occurrence {stats['new_occurrence_count']} · "
                        f"신규 문서 {stats['new_document_count']} · "
                        f"접근검사 {stats['access_checked_count']} "
                        f"(usable {stats['usable_count']}) · "
                        f"분석 큐 {stats['ai_queued_count']}"
                    )
                except Exception as e:  # noqa: BLE001 — 조합 실패는 기록하고 계속
                    conn.rollback()
                    failed.append(label)
                    msg = _safe_error(e)
                    repo.record_run(
                        conn, ch.source_id, ch.channel_id, "FAILED",
                        {"error_count": 1}, notes=msg[:500],
                        keyword=ch.keyword_term,
                    )
                    repo.mark_task_result(
                        conn, ch.channel_id, ch.keyword_term, ok=False, error=msg
                    )
                    repo.mark_channel_result(conn, ch.channel_id, ok=False, error=msg)
                    repo.mark_source_result(conn, ch.source_id, ok=False, error=msg)
                    conn.commit()
                    print(
                        f"[collect_reports] {label} 실패: {msg}",
                        file=sys.stderr,
                    )
    finally:
        conn.close()
    if failed:
        outcome = (
            "성공한 조합이 있어 정상 종료" if succeeded
            else "전부 실패 — 종료 코드 1"
        )
        print(
            f"[collect_reports] 실패 {len(failed)}조합 / 성공 {succeeded}조합 — "
            f"{outcome}: {', '.join(failed)}",
            file=sys.stderr,
        )
    return exit_code_for(succeeded, len(failed))


if __name__ == "__main__":
    raise SystemExit(main())
