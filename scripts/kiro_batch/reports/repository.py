"""보고서 4층 데이터모델 저장 계층 (스펙 §7·8).

occurrence upsert → canonical document 연결 → 접근성 기록 → 분석 큐 적재.
어댑터는 ReportCandidate만 넘기고, DB 사정은 전부 여기서 처리한다.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass

import psycopg

from ..reports.access import AccessResult
from ..reports.base import ReportCandidate
from ..reports.dedup import analysis_input_hash, document_fingerprint


@dataclass
class ChannelTask:
    """실행 대상 = (채널 × 검색어) 조합 1개 (2026-09-08).

    채널은 '수집원 × 하위 갈래' 템플릿이 되고(nanet 웹자료/세미나자료,
    point 참고/디지털), 검색어는 search_keywords 한 목록에서 온다.
    실행 단위가 채널이 아니라 조합이라 상태도 조합별로 기록한다
    (report_channel_keyword_runs).
    """

    source_id: str
    source_key: str
    source_priority: int
    channel_id: str
    channel_key: str
    adapter_key: str
    keyword_term: str  # '' = 검색어 미사용 채널(prism — 전체 조회 후 제목 프리필터)
    config: dict
    max_pages: int
    max_items: int
    request_interval_ms: int
    credential_key_name: str | None

    @property
    def query(self) -> str | None:
        """어댑터에 넘길 검색어. 미사용 채널은 None."""
        return self.keyword_term or None


# 마이그레이션 26 적용 여부 — 배치가 마이그레이션보다 먼저 배포돼도 죽지
# 않아야 한다. 한 번만 확인하고 프로세스 수명 동안 재사용한다.
_KEYWORD_MODEL: bool | None = None


def keyword_model_available(conn: psycopg.Connection) -> bool:
    global _KEYWORD_MODEL
    if _KEYWORD_MODEL is None:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT to_regclass('public.search_keywords') IS NOT NULL
                   AND to_regclass('public.report_channel_keyword_runs') IS NOT NULL
                   AND EXISTS (
                         SELECT 1 FROM information_schema.columns
                         WHERE table_schema = 'public'
                           AND table_name = 'report_source_channels'
                           AND column_name = 'uses_keywords') AS ok
                """
            )
            _KEYWORD_MODEL = bool(cur.fetchone()["ok"])
    return _KEYWORD_MODEL


def _task_from_row(r: dict, keyword_term: str) -> ChannelTask:
    return ChannelTask(
        source_id=str(r["source_id"]),
        source_key=r["source_key"],
        source_priority=int(r["source_priority"]),
        channel_id=str(r["channel_id"]),
        channel_key=r["channel_key"],
        adapter_key=r["adapter_key"] or "",
        keyword_term=keyword_term,
        config=r["config_json"] or {},
        max_pages=r["max_pages_per_run"],
        max_items=r["max_items_per_run"],
        request_interval_ms=r["request_interval_ms"],
        credential_key_name=r["credential_key_name"],
    )


# 정렬을 last_run_at 우선으로 바꾼 것이 핵심 수정이다 (2026-09-08).
# 종전 ORDER BY s.priority, c.channel_key + 상한 10에서는 nkis(priority 60)
# 채널이 사실상 실행되지 못했다 — 조합이 50개로 늘면 치명적이 된다.
_DUE_TASKS_SQL = """
WITH kw AS (
  SELECT term, sort_order
  FROM public.search_keywords
  WHERE scope IN ('ALL', 'REPORTS')
),
ch AS (
  SELECT s.id AS source_id, s.source_key, s.priority AS source_priority,
         c.id AS channel_id, c.channel_key,
         COALESCE(c.adapter_key, s.adapter_key) AS adapter_key,
         c.config_json, c.max_pages_per_run, c.max_items_per_run,
         c.request_interval_ms, c.credential_key_name,
         c.fetch_interval_hours, c.uses_keywords
  FROM public.report_source_channels c
  JOIN public.report_sources s ON s.id = c.source_id
  WHERE s.status = 'ACTIVE' AND c.enabled AND c.retired_at IS NULL
),
t AS (
  SELECT ch.*, kw.term AS keyword_term, kw.sort_order AS keyword_sort
  FROM ch JOIN kw ON ch.uses_keywords
  UNION ALL
  SELECT ch.*, ''::text AS keyword_term, 0 AS keyword_sort
  FROM ch WHERE NOT ch.uses_keywords
)
SELECT t.*
FROM t
LEFT JOIN public.report_channel_keyword_runs r
  ON r.channel_id = t.channel_id AND r.keyword_term = t.keyword_term
WHERE r.last_run_at IS NULL
   OR r.last_run_at < now() - make_interval(hours => t.fetch_interval_hours)
ORDER BY r.last_run_at ASC NULLS FIRST, t.source_priority ASC,
         t.keyword_sort ASC, t.keyword_term ASC
"""

# 마이그레이션 26 적용 전 경로 — 채널의 query를 그대로 검색어로 쓴다(종전 동작).
_DUE_CHANNELS_LEGACY_SQL = """
SELECT s.id AS source_id, s.source_key, s.priority AS source_priority,
       c.id AS channel_id, c.channel_key,
       COALESCE(c.adapter_key, s.adapter_key) AS adapter_key,
       c.query, c.config_json,
       c.max_pages_per_run, c.max_items_per_run,
       c.request_interval_ms, c.credential_key_name
FROM report_source_channels c
JOIN report_sources s ON s.id = c.source_id
WHERE s.status = 'ACTIVE'
  AND c.enabled
  AND (c.last_run_at IS NULL
       OR c.last_run_at < now() - make_interval(hours => c.fetch_interval_hours))
ORDER BY c.last_run_at ASC NULLS FIRST, s.priority ASC, c.channel_key ASC
"""


def fetch_due_tasks(conn: psycopg.Connection) -> list[ChannelTask]:
    """실행할 (채널 × 검색어) 조합. 오래 굶은 조합부터 돌려준다."""
    if not keyword_model_available(conn):
        print(
            "[collect_reports] 검색 키워드 모델 없음 — 채널의 기존 검색어로 실행합니다"
            " (supabase/migrations/20260908000026 미적용)",
            file=sys.stderr,
        )
        with conn.cursor() as cur:
            cur.execute(_DUE_CHANNELS_LEGACY_SQL)
            rows = cur.fetchall()
        return [_task_from_row(r, r["query"] or "") for r in rows]

    with conn.cursor() as cur:
        cur.execute(_DUE_TASKS_SQL)
        rows = cur.fetchall()
    return [_task_from_row(r, r["keyword_term"] or "") for r in rows]


def mark_task_result(
    conn: psycopg.Connection,
    channel_id: str,
    keyword_term: str,
    *,
    ok: bool,
    error: str | None = None,
) -> None:
    """(채널 × 검색어) 회전 상태 갱신. 이게 있어야 다음 실행이 다음 검색어로 넘어간다."""
    if not keyword_model_available(conn):
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.report_channel_keyword_runs
                   (channel_id, keyword_term, last_run_at, last_success_at, last_error)
            VALUES (%s, %s, now(), CASE WHEN %s THEN now() END, %s)
            ON CONFLICT (channel_id, keyword_term) DO UPDATE SET
              last_run_at = now(),
              last_success_at = CASE WHEN %s THEN now()
                                     ELSE report_channel_keyword_runs.last_success_at END,
              last_error = EXCLUDED.last_error
            """,
            (channel_id, keyword_term, ok, None if ok else (error or "")[:500], ok),
        )


def _metadata_hash(cand: ReportCandidate) -> str:
    key = "|".join(
        [
            cand.title,
            cand.institution or "",
            (cand.abstract or "")[:2000],
            cand.detail_url,
            ",".join(cand.candidate_download_urls),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def upsert_occurrence(
    conn: psycopg.Connection,
    source_id: str,
    channel_id: str,
    cand: ReportCandidate,
) -> tuple[str, bool, bool]:
    """(occurrence_id, is_new, metadata_changed) 반환. 재발견은 last_seen만 갱신."""
    meta_hash = _metadata_hash(cand)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO report_occurrences (
              source_id, channel_id, external_id, title, institution, authors,
              published_date, published_year, source_report_type,
              source_abstract, source_keywords, detail_url,
              candidate_download_url, metadata_hash
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (source_id, external_id) DO UPDATE SET
              title = EXCLUDED.title,
              institution = COALESCE(EXCLUDED.institution, report_occurrences.institution),
              authors = CASE WHEN EXCLUDED.authors <> '{}'
                             THEN EXCLUDED.authors ELSE report_occurrences.authors END,
              published_date = COALESCE(EXCLUDED.published_date, report_occurrences.published_date),
              published_year = COALESCE(EXCLUDED.published_year, report_occurrences.published_year),
              source_abstract = COALESCE(EXCLUDED.source_abstract, report_occurrences.source_abstract),
              source_keywords = COALESCE(EXCLUDED.source_keywords, report_occurrences.source_keywords),
              candidate_download_url = COALESCE(EXCLUDED.candidate_download_url,
                                                report_occurrences.candidate_download_url),
              metadata_hash = EXCLUDED.metadata_hash,
              last_seen_at = now(),
              updated_at = now()
            RETURNING id, (xmax = 0) AS inserted
            """,
            (
                source_id,
                channel_id,
                cand.external_id,
                cand.title,
                cand.institution,
                cand.authors,
                cand.published_date,
                cand.published_year,
                cand.source_report_type,
                cand.abstract,
                json.dumps(cand.source_keywords, ensure_ascii=False)
                if cand.source_keywords
                else None,
                cand.detail_url,
                cand.candidate_download_urls[0]
                if cand.candidate_download_urls
                else None,
                meta_hash,
            ),
        )
        row = cur.fetchone()
    # RETURNING의 metadata_hash 비교는 갱신 후 값이라 항상 same — 신규 여부만 신뢰.
    # 변경 감지는 upsert 전 SELECT 비용을 아끼려 신규=변경으로 취급한다.
    return str(row["id"]), bool(row["inserted"]), bool(row["inserted"])


def link_document(
    conn: psycopg.Connection, occurrence_id: str, cand: ReportCandidate
) -> tuple[str, bool]:
    """occurrence를 canonical Document에 연결. (document_id, is_new_document)."""
    fp = document_fingerprint(cand.title, cand.institution, cand.published_year)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO report_documents (
              canonical_title, institution, authors, published_date,
              published_year, best_abstract, preferred_occurrence_id,
              document_fingerprint
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (document_fingerprint) DO UPDATE SET
              best_abstract = CASE
                WHEN COALESCE(length(EXCLUDED.best_abstract), 0)
                     > COALESCE(length(report_documents.best_abstract), 0)
                THEN EXCLUDED.best_abstract ELSE report_documents.best_abstract END,
              published_date = COALESCE(report_documents.published_date,
                                        EXCLUDED.published_date),
              updated_at = now()
            RETURNING id, (xmax = 0) AS inserted
            """,
            (
                cand.title,
                cand.institution,
                cand.authors,
                cand.published_date,
                cand.published_year,
                cand.abstract,
                occurrence_id,
                fp,
            ),
        )
        doc = cur.fetchone()
        cur.execute(
            "UPDATE report_occurrences SET document_id = %s, updated_at = now() "
            "WHERE id = %s",
            (doc["id"], occurrence_id),
        )
    return str(doc["id"]), bool(doc["inserted"])


def update_access(
    conn: psycopg.Connection, occurrence_id: str, result: AccessResult
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE report_occurrences SET
              access_status = %s,
              access_checked_at = now(),
              access_http_status = %s,
              access_note = %s,
              file_format = COALESCE(%s, file_format),
              file_name = COALESCE(%s, file_name),
              candidate_download_url = COALESCE(%s, candidate_download_url),
              updated_at = now()
            WHERE id = %s
            """,
            (
                result.status,
                result.http_status,
                result.note,
                result.file_format,
                result.file_name,
                result.final_url,
                occurrence_id,
            ),
        )


def queue_analysis(
    conn: psycopg.Connection,
    document_id: str,
    cand: ReportCandidate,
    priority: int = 100,
) -> bool:
    """같은 입력으로 이미 분석돼 있으면 스킵 (스펙 §27). 큐 적재 시 True.

    priority: CORE(로봇 핵심) 100 / TOOL(드론·AI 등 인접) 200 —
    쿼터가 부족하면 핵심부터 분석된다 (사용자: 중요성의 차이).
    """
    input_hash = analysis_input_hash(
        cand.title,
        cand.institution,
        cand.abstract,
        cand.source_report_type,
        cand.source_keywords,
        cand.published_date.isoformat() if cand.published_date else None,
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.input_hash
            FROM report_documents d
            LEFT JOIN report_analyses a ON a.id = d.current_analysis_id
            WHERE d.id = %s
            """,
            (document_id,),
        )
        row = cur.fetchone()
        if row and row["input_hash"] == input_hash:
            return False
        cur.execute(
            """
            INSERT INTO report_analysis_jobs (report_document_id, input_hash, priority)
            VALUES (%s, %s, %s)
            ON CONFLICT (report_document_id) DO UPDATE SET
              status = CASE WHEN report_analysis_jobs.status IN ('DONE','FAILED')
                            AND report_analysis_jobs.input_hash
                                IS DISTINCT FROM EXCLUDED.input_hash
                       THEN 'PENDING' ELSE report_analysis_jobs.status END,
              input_hash = EXCLUDED.input_hash,
              priority = LEAST(report_analysis_jobs.priority, EXCLUDED.priority),
              updated_at = now()
            RETURNING status
            """,
            (document_id, input_hash, priority),
        )
        status = cur.fetchone()["status"]
        if status == "PENDING":
            cur.execute(
                "UPDATE report_documents SET analysis_status = 'QUEUED', "
                "updated_at = now() WHERE id = %s AND analysis_status <> 'DONE'",
                (document_id,),
            )
    return status == "PENDING"


def record_run(
    conn: psycopg.Connection,
    source_id: str,
    channel_id: str | None,
    status: str,
    stats: dict[str, int],
    notes: str | None = None,
    keyword: str | None = None,
) -> None:
    cols = [
        "fetched_count", "new_occurrence_count", "new_document_count",
        "duplicate_count", "prefilter_pass_count", "prefilter_excluded_count",
        "access_checked_count", "usable_count", "ai_queued_count",
        "error_count",
    ]
    # keyword_term은 마이그레이션 26에서 생긴 칸이다 — 미적용 환경에서는 뺀다.
    extra_cols = ", keyword_term" if keyword_model_available(conn) else ""
    extra_vals = ", %s" if extra_cols else ""
    tail: tuple = (keyword or None,) if extra_cols else ()
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO report_source_runs (
              source_id, channel_id, finished_at, status, {", ".join(cols)}, notes{extra_cols}
            )
            VALUES (%s, %s, now(), %s, {", ".join(["%s"] * len(cols))}, %s{extra_vals})
            """,
            (source_id, channel_id, status)
            + tuple(stats.get(c, 0) for c in cols)
            + (notes,)
            + tail,
        )


def mark_channel_result(
    conn: psycopg.Connection, channel_id: str, ok: bool, error: str | None = None
) -> None:
    with conn.cursor() as cur:
        if ok:
            cur.execute(
                "UPDATE report_source_channels SET last_run_at = now(), "
                "last_success_at = now(), last_error = NULL, updated_at = now() "
                "WHERE id = %s",
                (channel_id,),
            )
        else:
            cur.execute(
                "UPDATE report_source_channels SET last_run_at = now(), "
                "last_error = %s, updated_at = now() WHERE id = %s",
                ((error or "")[:500], channel_id),
            )


def mark_source_result(
    conn: psycopg.Connection, source_id: str, ok: bool, error: str | None = None
) -> None:
    with conn.cursor() as cur:
        if ok:
            cur.execute(
                "UPDATE report_sources SET last_collected_at = now(), "
                "last_success_at = now(), consecutive_failures = 0, "
                "last_error = NULL, updated_at = now() WHERE id = %s",
                (source_id,),
            )
        else:
            cur.execute(
                "UPDATE report_sources SET last_collected_at = now(), "
                "consecutive_failures = consecutive_failures + 1, "
                "last_error = %s, updated_at = now() WHERE id = %s",
                ((error or "")[:500], source_id),
            )
