"""workflow_usage 계측 (설계 §10.5, tasks §15.1).

각 배치 실행의 단계별 소요시간과 처리 건수를 기록해
운영 대시보드와 Actions 예산 관리에 사용한다.
"""

from __future__ import annotations

import os
import time

import psycopg


STALE_RUN_MINUTES = 30


def close_stale_runs(conn: psycopg.Connection, minutes: int = STALE_RUN_MINUTES) -> int:
    """RUNNING으로 굳은 실행 기록을 정리한다 (2026-08-11).

    GitHub이 잡을 강제 종료하면(동시 실행 취소·timeout·러너 사망) 파이썬
    프로세스가 SIGKILL되어 finally의 finish()가 실행되지 않는다. 그러면 이
    행이 영원히 RUNNING으로 남아 운영 화면에 '실행 중'으로 보인다 —
    실측으로 8건이 최대 5일째 남아 있었다.
    가장 긴 배치가 10분이므로 30분을 넘겼으면 확실히 죽은 것이다.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE workflow_usage
            SET status = 'FAILURE',
                completed_at = now(),
                keepalive_status = coalesce(keepalive_status, '') ||
                    '중단됨(기록 미완료 — 강제 종료 추정)'
            WHERE status = 'RUNNING'
              AND started_at < now() - make_interval(mins => %s)
            """,
            (minutes,),
        )
        closed = cur.rowcount
    conn.commit()
    return closed


class UsageRecorder:
    def __init__(
        self,
        conn: psycopg.Connection,
        workflow_name: str,
        note: str | None = None,
    ):
        self.conn = conn
        self.workflow_name = workflow_name
        self.run_id = os.environ.get("GITHUB_RUN_ID")
        self.started = time.monotonic()
        self.stage_seconds: dict[str, int] = {}
        self.counts = {"collected": 0, "analyzed": 0, "api_calls": 0}
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO workflow_usage (workflow_name, workflow_run_id, status, note)
                VALUES (%s, %s, 'RUNNING', %s)
                RETURNING id
                """,
                (workflow_name, self.run_id, note),
            )
            self.usage_id = str(cur.fetchone()["id"])
        conn.commit()

    def record_stage(self, stage: str, seconds: float) -> None:
        """stage: fetch | normalize | cluster | analysis"""
        self.stage_seconds[stage] = int(seconds)

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started

    def finish(
        self,
        status: str,
        remaining_pending: int | None = None,
        oldest_pending_minutes: int | None = None,
        keepalive_performed: bool = False,
        keepalive_status: str | None = None,
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE workflow_usage
                SET completed_at = now(),
                    duration_seconds = %s,
                    fetch_duration_seconds = %s,
                    normalize_duration_seconds = %s,
                    cluster_duration_seconds = %s,
                    analysis_duration_seconds = %s,
                    collected_count = %s,
                    analyzed_count = %s,
                    api_call_count = %s,
                    remaining_pending_count = %s,
                    oldest_pending_age_minutes = %s,
                    keepalive_performed = %s,
                    keepalive_status = %s,
                    status = %s
                WHERE id = %s
                """,
                (
                    int(self.elapsed_seconds()),
                    self.stage_seconds.get("fetch"),
                    self.stage_seconds.get("normalize"),
                    self.stage_seconds.get("cluster"),
                    self.stage_seconds.get("analysis"),
                    self.counts["collected"],
                    self.counts["analyzed"],
                    self.counts["api_calls"],
                    remaining_pending,
                    oldest_pending_minutes,
                    keepalive_performed,
                    keepalive_status,
                    status,
                    self.usage_id,
                ),
            )
        self.conn.commit()
