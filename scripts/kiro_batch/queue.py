"""PostgreSQL 작업 큐 (설계 §9, tasks §7).

- FOR UPDATE SKIP LOCKED로 원자적 잠금
- 30분 초과 PROCESSING 자동 복구 (cleanup 주기에 의존하지 않음)
- 429는 30초 → 2분 → 다음 배치 이월, 일일 한도 추정 시 DEFERRED
"""

from __future__ import annotations

from datetime import date, datetime

import psycopg

# 출처 유형 → 큐 우선순위 (설계 §9.2)
# 그 외: LOW_PRIORITY 필터 통과분 = 원래 값 +5, 재분석 백필 = 60 (requeue_analysis)
SOURCE_TYPE_PRIORITY = {
    "정부·공공기관": 10,
    "정책·사업 공고": 10,
    "기업 공식 발표": 20,
    "연구기관·대학": 20,
    "전문 기술 큐레이션 매체": 30,
    "로봇·산업 전문매체": 30,
    "일반 언론": 40,
}

STALE_LOCK_MINUTES = 30
MAX_SERVER_ERROR_ATTEMPTS = 5


def recover_stale_locks(conn: psycopg.Connection) -> int:
    """매 분석 배치 시작 시 실행 (설계 §9.3). 복구 건수를 반환한다."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE analysis_jobs
            SET status = 'RETRY',
                locked_at = NULL,
                locked_by = NULL,
                available_at = now(),
                last_error_code = 'STALE_LOCK',
                last_error_message = '30분 이상 처리 상태가 유지되어 자동 복구됨'
            WHERE status = 'PROCESSING'
              AND locked_at < now() - interval '30 minutes'
            """
        )
        recovered = cur.rowcount
    conn.commit()
    return recovered


def enqueue_article_job(
    conn: psycopg.Connection,
    cluster_id: str,
    priority: int,
    source_published_at: datetime | None = None,
) -> str | None:
    """클러스터당 ARTICLE 작업 1개. 중복 생성은 UNIQUE 제약이 방지한다.

    source_published_at은 신선도 정렬용 비정규화 값(0021). 미상이면 적재
    시각(now)으로 채운다 — NULL로 두면 정렬에서 영구 최하위가 되어 굶는다.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO analysis_jobs
              (cluster_id, job_type, priority, source_published_at)
            VALUES (%s, 'ARTICLE', %s, coalesce(%s::timestamptz, now()))
            ON CONFLICT (cluster_id, job_type) DO NOTHING
            RETURNING id
            """,
            (cluster_id, priority, source_published_at),
        )
        row = cur.fetchone()
    conn.commit()
    return str(row["id"]) if row else None


def acquire_next_job(
    conn: psycopg.Connection, worker_id: str, prefer_fresh: bool = True
) -> dict | None:
    """다음 작업을 원자적으로 잠근다 (설계 §9.4). 없으면 None.

    prefer_fresh=True(기본): 계층 안에서 **원문이 최신인 것부터**. 아침 배치가
    전날 저녁분만 처리하고 끝나던 문제의 직접 해법 (2026-08-11).
    prefer_fresh=False: 오래 기다린 것부터 — 호출자가 배치의 일부 슬롯을
    여기에 배정해 신선도 정렬로 인한 기아를 막는다 (freshness.starvation_slots).

    계층은 priority를 10단위로 묶은 값이다. p40(일반)과 p45(저확신)를 한
    계층으로 보는 이유는 freshness.freshness_tier 주석 참고.
    """
    order_by = (
        "((priority / 10) * 10) ASC, source_published_at DESC"
        if prefer_fresh
        else "((priority / 10) * 10) ASC, created_at ASC"
    )
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id
            FROM analysis_jobs
            WHERE status IN ('PENDING', 'RETRY', 'DEFERRED')
              AND available_at <= now()
            ORDER BY {order_by}
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row is None:
            conn.commit()
            return None
        cur.execute(
            """
            UPDATE analysis_jobs
            SET status = 'PROCESSING',
                locked_at = now(),
                locked_by = %s,
                attempt_count = attempt_count + 1
            WHERE id = %s
            RETURNING id, cluster_id, job_type, priority, attempt_count
            """,
            (worker_id, row["id"]),
        )
        job = cur.fetchone()
    conn.commit()
    return job


def complete_job(conn: psycopg.Connection, job_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE analysis_jobs
            SET status = 'DONE', locked_at = NULL, locked_by = NULL,
                last_error_code = NULL, last_error_message = NULL
            WHERE id = %s
            """,
            (job_id,),
        )
    conn.commit()


def retry_job_after_rate_limit(
    conn: psycopg.Connection, job_id: str, attempt_count: int
) -> None:
    """429 재시도: 1차 30초, 2차 2분, 이후 다음 배치 이월 (tasks §7.5)."""
    if attempt_count <= 1:
        delay = "30 seconds"
    elif attempt_count == 2:
        delay = "2 minutes"
    else:
        delay = "3 hours"  # 다음 수집·분석 배치로 이월
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE analysis_jobs
            SET status = 'RETRY', locked_at = NULL, locked_by = NULL,
                available_at = now() + interval '{delay}',
                last_error_code = 'RATE_LIMITED',
                last_error_message = 'Gemini 429 — 한도 초과로 지연 재시도'
            WHERE id = %s
            """,
            (job_id,),
        )
    conn.commit()


def defer_job_until_next_quota_day(conn: psycopg.Connection, job_id: str) -> None:
    """일일 한도 추정 도달 — PT 날짜가 바뀐 뒤 처리하도록 연기."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE analysis_jobs
            SET status = 'DEFERRED', locked_at = NULL, locked_by = NULL,
                available_at = now() + interval '6 hours',
                last_error_code = 'DAILY_QUOTA',
                last_error_message = '일일 내부 목표 도달로 연기됨'
            WHERE id = %s
            """,
            (job_id,),
        )
    conn.commit()


def fail_job(
    conn: psycopg.Connection,
    job_id: str,
    attempt_count: int,
    error_code: str,
    error_message: str,
    retryable: bool = True,
) -> None:
    """서버 오류는 지수 백오프 최대 5회, 이후 FAILED (tasks §7.5)."""
    if retryable and attempt_count < MAX_SERVER_ERROR_ATTEMPTS:
        backoff_minutes = min(2 ** attempt_count, 60)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analysis_jobs
                SET status = 'RETRY', locked_at = NULL, locked_by = NULL,
                    available_at = now() + make_interval(mins => %s),
                    last_error_code = %s,
                    last_error_message = left(%s, 1000)
                WHERE id = %s
                """,
                (backoff_minutes, error_code, error_message, job_id),
            )
    else:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analysis_jobs
                SET status = 'FAILED', locked_at = NULL, locked_by = NULL,
                    last_error_code = %s,
                    last_error_message = left(%s, 1000)
                WHERE id = %s
                """,
                (error_code, error_message, job_id),
            )
    conn.commit()


def count_pending(conn: psycopg.Connection) -> tuple[int, int | None]:
    """(대기 건수, 가장 오래된 대기 분) — workflow_usage 기록용."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS cnt,
                   floor(extract(epoch FROM (now() - min(created_at))) / 60)::int AS oldest_minutes
            FROM analysis_jobs
            WHERE status IN ('PENDING', 'RETRY', 'DEFERRED')
            """
        )
        row = cur.fetchone()
    return row["cnt"], row["oldest_minutes"]


def record_gemini_call(
    conn: psycopg.Connection,
    model_name: str,
    quota_date: date,
    status: str,
    error_code: str | None = None,
    job_id: str | None = None,
    *,
    purpose: str | None = None,
    requested_model: str | None = None,
    served_model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    """telemetry 컬럼(0019)은 전부 선택 — 기존 호출부는 그대로 동작한다."""
    total = (
        input_tokens + output_tokens
        if input_tokens is not None and output_tokens is not None
        else None
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gemini_calls (
              model_name, quota_date_pt, status, error_code, job_id,
              purpose, requested_model, served_model,
              input_tokens, output_tokens, total_tokens
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                model_name, quota_date, status, error_code, job_id,
                purpose, requested_model, served_model,
                input_tokens, output_tokens, total,
            ),
        )
    conn.commit()


def todays_call_count(
    conn: psycopg.Connection, model_name: str, quota_date: date
) -> int:
    """PT 날짜 기준 오늘 호출 수 (설계 §7.1)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT coalesce(sum(request_count), 0) AS cnt
            FROM gemini_calls
            WHERE model_name = %s AND quota_date_pt = %s
            """,
            (model_name, quota_date),
        )
        return int(cur.fetchone()["cnt"])
