"""PostgreSQL 연결 헬퍼 (psycopg 3, 직접 연결).

배치는 Supabase service role 경로(직접 DB 연결)를 사용하므로 RLS를 우회한다.
연결 문자열은 SUPABASE_DB_URL 환경변수로 관리한다.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row


def connect(db_url: str) -> psycopg.Connection:
    """배치용 연결.

    타임아웃·keepalive는 배치가 죽은 연결에 매달리는 것을 막는다 — GitHub
    러너(미국)와 Supabase(서울) 사이라 왕복이 길고, 실측으로 배치 하나가
    응답 없는 구간에 300초를 버린 사례가 있다 (2026-08-11).
    statement_timeout 60초는 dedup_sweep의 자기조인까지 감안한 값이다.
    """
    if not db_url:
        raise RuntimeError(
            "SUPABASE_DB_URL이 설정되지 않았습니다 — .env 또는 GitHub Secrets를 확인하세요."
        )
    return psycopg.connect(
        db_url,
        row_factory=dict_row,
        connect_timeout=15,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
        options="-c statement_timeout=60000 -c idle_in_transaction_session_timeout=120000",
    )


def load_app_settings(conn: psycopg.Connection) -> dict[str, object]:
    with conn.cursor() as cur:
        cur.execute("SELECT key, value FROM app_settings")
        return {row["key"]: row["value"] for row in cur.fetchall()}
