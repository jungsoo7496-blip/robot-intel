"""일일 정리 배치 (tasks §16.5 → 2026-09-08 데이터 관리 확장).

배경: DB가 296→308 MB/500 MB(실측 2026-09-07), 하루 +8 MB로 10월 초 한도에
닿으면 Supabase가 읽기 전용이 된다. 용량의 주범은 읽는 코드가 없는 복제
컬럼(raw_items.raw_text 40 MB, analyses.raw_response 26 MB)과 EXCLUDE·비대표
본문, 그리고 발행 7일이 지난 추출 대기 적체였다.

매일 KST 새벽에 돈다 (cleanup.yml). 게시 데이터·브리프·분석 결과는 삭제하지
않는다. 보존 정책 값은 app_settings — 운영 화면(/admin/data)에서 편집.

일일 작업:
- raw_text 보존기간(raw_text_retention_days) 지난 것 NULL  (기존)
- EXCLUDE 판정 본문 NULL                                   (keep_exclude_body=false)
- 클러스터 비대표 구성원 본문 NULL                          (nonrep_body_retention_days,
  publish.MERGE_WINDOW_DAYS보다 짧으면 그 값으로 올림 — 2차 병합 재선정 경로)
- 게시 카드 없는 클러스터의 analyses 삭제 + 대표 본문 NULL  (unpublished_retention_days,
  2026-09-22 — 금고는 게시 기사만 담으므로 여기서 비운다; 행·제목·링크·구성원은 남김)
- 분석 대기 job 만료 → CANCELLED                            (analysis_expire_days)
- EXPIRED·EXCLUDE·FAILED raw_items 행 삭제                  (90일 경과 + 클러스터 미소속)
- 오래된 source_runs·gemini_calls 정리                       (기존)
- PASS 분석의 raw_response NULL                             (keep_raw_response=false —
  쓰기 경로가 설정을 안 지켜 하루 0.7 MiB씩 쌓이던 누수, 2026-09-17)
- 파일 금고 (vault.py, 2026-09-17): 반달 기간(1~15일 / 16일~말일)의 마지막 날 +
  vault_window_days(하한 14) 지난 기간을 Storage 'vault' 버킷에 내보내고 같은
  실행에서 재다운로드 검증 → (vault_prune_enabled 이고 Storage·Release 검증이
  모두 끝난 기간만) 부속을 DB에서 지움(cluster_members는 남김) → 자가점검

일회성 작업 (env CLEANUP_TASKS 쉼표 목록 — 운영 화면 버튼이 workflow_dispatch
input으로 넘긴다):
- null_raw_text_all       raw_text 전량 NULL (2,000행 배치)
- null_raw_response       PASS 분석의 raw_response NULL
- expire_extract_backlog  추출 대기 만료 (extract_expire_days, 0이면 3일)
- cancel_stale_jobs       분석 대기 만료 (analysis_expire_days, 0이면 3일)

VACUUM FULL은 여기서 하지 않는다 — Actions 실행 시간과 테이블 잠금 때문.
NULL로 비운 공간은 VACUUM FULL 전에는 DB 용량 수치에 반영되지 않으므로,
사무실 PC에서 scripts/db_vacuum_full.bat(→ `--vacuum-full`)을 배치 없는 시간에
돌린다. 같은 실행이 카드 표(published_items)의 색인을 REINDEX CONCURRENTLY로
무중단 재구축한다 — 금고 정리(카드 전량 UPDATE) 뒤 두 배로 부푼 trigram 색인 회수.

실행: python -m kiro_batch.cleanup [--tasks a,b] [--vacuum-full]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable

import psycopg
from psycopg.rows import dict_row

from . import vault
from .config import Settings
from .db import connect, load_app_settings
from .publish import MERGE_WINDOW_DAYS
from .usage import UsageRecorder

SOURCE_RUN_RETENTION_DAYS = 90
GEMINI_CALL_RETENTION_DAYS = 120
# EXPIRED·EXCLUDE·FAILED 원문 행을 지우기까지의 유예 — 그 전에는 canonical_url
# UNIQUE가 같은 URL의 재수집을 막아 주는 역할을 한다
DEAD_ROW_RETENTION_DAYS = 90
# 큰 UPDATE·DELETE의 1회 처리 행수. db.connect의 statement_timeout 60초 안에
# 한 배치가 끝나야 하고, 잠금을 오래 잡지 않아야 한다 (수집 배치와 겹칠 수 있다)
BATCH_ROWS = 2000
# 폭주 방지 — 2,000 × 500 = 100만 행. 넘으면 다음 실행이 이어서 한다
MAX_BATCHES = 500
# 일회성 만료 작업에서 설정값이 0(=일일 자동 만료 안 함)일 때 쓰는 기준 일수.
# 운영자가 버튼을 눌러 명시적으로 요청한 것이므로 아무것도 안 하지 않는다.
DEFAULT_EXPIRE_DAYS = 3

ONE_OFF_TASKS = (
    "null_raw_text_all",
    "null_raw_response",
    "expire_extract_backlog",
    "cancel_stale_jobs",
)

# 분석 큐에서 '아직 살아 있는' 상태 — 이런 job이 걸린 클러스터의 본문은 건드리지 않는다
ACTIVE_JOB_STATUSES = ("PENDING", "PROCESSING", "RETRY", "DEFERRED")


# ------------------------------------------------------------
# 순수 판정 함수 (DB 없이 단위 테스트 — tests/unit/test_cleanup_rules.py)
# ------------------------------------------------------------
def parse_tasks(raw: str | None) -> tuple[list[str], list[str]]:
    """CLEANUP_TASKS 파싱 → (유효한 작업, 순서 유지·중복 제거), (모르는 이름)."""
    if not raw:
        return [], []
    valid: list[str] = []
    unknown: list[str] = []
    for token in raw.split(","):
        name = token.strip().lower()
        if not name:
            continue
        if name in ONE_OFF_TASKS:
            if name not in valid:
                valid.append(name)
        elif name not in unknown:
            unknown.append(name)
    return valid, unknown


def nonrep_retention_days(
    setting_days: int, merge_window_days: int = MERGE_WINDOW_DAYS
) -> int:
    """비대표 본문 보존 일수. 0 이하 = 안 함.

    2차 병합(publish._merge_into_existing)은 병합 창 안의 게시물에 새 기사를
    흡수하면서 대표 출처를 다시 뽑는다. 그때 비대표였던 구성원이 대표가 되면
    그 본문으로 재분석하므로, 창이 닫히기 전에는 비워선 안 된다.
    """
    if setting_days <= 0:
        return 0
    return max(setting_days, merge_window_days)


def expire_days_or_default(
    setting_days: int, default: int = DEFAULT_EXPIRE_DAYS
) -> int:
    """일회성 만료 작업의 기준 일수 — 설정이 0(자동 만료 안 함)이면 기본값."""
    return setting_days if setting_days > 0 else default


def usage_note(counts: dict[str, int], errors: dict[str, str]) -> str:
    """workflow_usage.note 한 줄 요약 — 운영 화면(사용량 표)에서 그대로 보인다."""
    parts = [f"{label} {n}건" for label, n in counts.items()]
    parts += [f"{label} 실패({msg[:60]})" for label, msg in errors.items()]
    return " · ".join(parts)[:900] if parts else "할 일 없음"


def run_status(counts: dict[str, int], errors: dict[str, str]) -> str:
    """workflow_usage.status — 하나라도 실패면 PARTIAL, 전부 실패면 FAILURE."""
    if not errors:
        return "SUCCESS"
    return "PARTIAL" if counts else "FAILURE"


# ------------------------------------------------------------
# DB 작업
# ------------------------------------------------------------
def _run_batched(conn: psycopg.Connection, sql: str, params: tuple, label: str) -> int:
    """`... WHERE id IN (SELECT ... LIMIT %s)` 꼴의 문장을 배치 단위로 반복한다.

    sql의 마지막 플레이스홀더가 LIMIT이어야 한다. 배치마다 커밋하므로 중간에
    죽어도 그때까지의 결과는 남고, 다음 실행이 이어서 한다 (멱등).
    """
    total = 0
    for _ in range(MAX_BATCHES):
        with conn.cursor() as cur:
            cur.execute(sql, (*params, BATCH_ROWS))
            n = cur.rowcount
        conn.commit()
        total += n
        if n < BATCH_ROWS:
            break
    else:
        print(f"[cleanup] {label}: 배치 상한 {MAX_BATCHES}회 도달 — 나머지는 다음 실행에서")
    return total


def null_raw_text_expired(conn: psycopg.Connection, retention_days: int) -> int:
    """raw_text 보존기간 경과분 NULL (기존 작업)."""
    if retention_days <= 0:
        return 0
    return _run_batched(
        conn,
        """
        UPDATE raw_items SET raw_text = NULL
        WHERE id IN (
          SELECT id FROM raw_items
          WHERE raw_text IS NOT NULL
            AND fetched_at < now() - make_interval(days => %s)
          LIMIT %s
        )
        """,
        (retention_days,),
        "raw_text 보존기간",
    )


def null_raw_text_all(conn: psycopg.Connection) -> int:
    """[일회성] raw_text 전량 NULL — collect.py가 더 이상 쓰지 않는 복제 컬럼."""
    return _run_batched(
        conn,
        """
        UPDATE raw_items SET raw_text = NULL
        WHERE id IN (SELECT id FROM raw_items WHERE raw_text IS NOT NULL LIMIT %s)
        """,
        (),
        "raw_text 전량",
    )


def null_exclude_body(conn: psycopg.Connection, keep_exclude_body: bool) -> int:
    """EXCLUDE 판정 항목의 본문 NULL — 분석·클러스터 대상이 아니라 읽는 코드가 없다."""
    if keep_exclude_body:
        return 0
    return _run_batched(
        conn,
        """
        UPDATE raw_items SET clean_text = NULL, raw_text = NULL
        WHERE id IN (
          SELECT id FROM raw_items
          WHERE filter_status = 'EXCLUDE' AND clean_text IS NOT NULL
          LIMIT %s
        )
        """,
        (),
        "EXCLUDE 본문",
    )


def null_nonrep_body(conn: psycopg.Connection, retention_days: int) -> int:
    """클러스터 비대표 구성원 본문 NULL.

    조건 (모두 만족):
    - 클러스터 생성이 N일 초과 — 1차 클러스터링 후보 창(7일) 밖
    - 게시물이 있으면 게시도 N일 초과 — 2차 병합 창 밖 (없으면 통과)
    - 클러스터 대표가 아님 (cluster_members.is_representative·content_clusters 양쪽 확인)
    - 살아 있는 분석 job이 없음 — 재분석·2차 병합 대표 재선정 경로 보호
    분석 프롬프트는 대표 본문만 쓴다 (analyze.build_article_prompt).
    """
    if retention_days <= 0:
        return 0
    return _run_batched(
        conn,
        """
        UPDATE raw_items SET clean_text = NULL, raw_text = NULL
        WHERE id IN (
          SELECT ri.id
          FROM raw_items ri
          JOIN cluster_members cm
            ON cm.raw_item_id = ri.id AND cm.is_representative = false
          JOIN content_clusters c ON c.id = cm.cluster_id
          LEFT JOIN published_items p ON p.cluster_id = c.id
          WHERE ri.clean_text IS NOT NULL
            AND c.representative_raw_item_id IS DISTINCT FROM ri.id
            AND c.created_at < now() - make_interval(days => %s)
            AND (p.published_at IS NULL
                 OR p.published_at < now() - make_interval(days => %s))
            AND NOT EXISTS (
              SELECT 1 FROM analysis_jobs j
              WHERE j.cluster_id = c.id AND j.status = ANY(%s)
            )
          LIMIT %s
        )
        """,
        (retention_days, retention_days, list(ACTIVE_JOB_STATUSES)),
        "비대표 본문",
    )


def null_unpublished_bulk(conn: psycopg.Connection, retention_days: int) -> int:
    """게시 카드가 없는 클러스터의 부속 비우기 — analyses 행 삭제 + 대표 본문 NULL.

    금고(vault)는 게시된 기사만 내보낸다. 그래서 AI가 로봇 뉴스가 아니라고 판정한
    클러스터와 다른 클러스터에 병합된 클러스터의 분석 결과·대표 본문은 어디서도
    안 읽히는데 영구히 남았다 (2026-09-22 실측 analyses 10,580행 23 MiB + 대표 본문
    1,901행 4 MiB, 하루 +186행 — 분석 7슬롯 뒤 두 배). 클러스터 행·raw_items 행
    (제목·링크·수집 기록)·cluster_members는 남긴다 — 기사 자체를 지우는 게 아니다.

    조건 (모두 만족):
    - 클러스터 생성이 N일 초과 — 2차 병합 창(7일)과 검토 여유 밖
    - published_items 없음 (게시된 클러스터의 부속은 금고가 맡는다)
    - 살아 있는 분석 job 없음 — 재분석 중이면 다음 실행에서
    - 어떤 카드도 current_analysis_id로 그 analyses 행을 가리키지 않음 (FK가 SET NULL
      이라 오류는 안 나지만 카드 본문이 사라지므로 한 번 더 막는다)
    대표 본문은 추가로: 같은 raw_item을 대표로 둔 다른 클러스터가 아직 젊거나 미금고
    카드가 있거나(vault C5와 같은 규칙) job이 살아 있으면 보호, 구성원으로 속한 어느
    클러스터에도 살아 있는 job이 없을 때만 비운다.
    """
    if retention_days <= 0:
        return 0
    active = list(ACTIVE_JOB_STATUSES)
    deleted = _run_batched(
        conn,
        """
        DELETE FROM analyses WHERE id IN (
          SELECT a.id
          FROM analyses a
          JOIN content_clusters c ON c.id = a.cluster_id
          WHERE c.created_at < now() - make_interval(days => %s)
            AND NOT EXISTS (SELECT 1 FROM published_items p WHERE p.cluster_id = c.id)
            AND NOT EXISTS (
              SELECT 1 FROM published_items p WHERE p.current_analysis_id = a.id
            )
            AND NOT EXISTS (
              SELECT 1 FROM analysis_jobs j
              WHERE j.cluster_id = c.id AND j.status = ANY(%s)
            )
          LIMIT %s
        )
        """,
        (retention_days, active),
        "미게시 분석",
    )
    nulled = _run_batched(
        conn,
        """
        UPDATE raw_items SET clean_text = NULL, raw_text = NULL
        WHERE id IN (
          SELECT ri.id
          FROM raw_items ri
          JOIN content_clusters c ON c.representative_raw_item_id = ri.id
          WHERE ri.clean_text IS NOT NULL
            AND c.created_at < now() - make_interval(days => %s)
            AND NOT EXISTS (SELECT 1 FROM published_items p WHERE p.cluster_id = c.id)
            AND NOT EXISTS (
              SELECT 1 FROM analysis_jobs j
              WHERE j.cluster_id = c.id AND j.status = ANY(%s)
            )
            AND NOT EXISTS (
              SELECT 1 FROM content_clusters c2
              WHERE c2.representative_raw_item_id = ri.id AND c2.id <> c.id
                AND (
                  c2.created_at >= now() - make_interval(days => %s)
                  OR EXISTS (
                    SELECT 1 FROM published_items p2
                    WHERE p2.cluster_id = c2.id AND p2.vaulted_at IS NULL
                  )
                  OR EXISTS (
                    SELECT 1 FROM analysis_jobs j2
                    WHERE j2.cluster_id = c2.id AND j2.status = ANY(%s)
                  )
                )
            )
            AND NOT EXISTS (
              SELECT 1 FROM cluster_members cm3
              JOIN analysis_jobs j3 ON j3.cluster_id = cm3.cluster_id
              WHERE cm3.raw_item_id = ri.id AND j3.status = ANY(%s)
            )
          LIMIT %s
        )
        """,
        (retention_days, active, retention_days, active, active),
        "미게시 대표 본문",
    )
    return deleted + nulled


def cancel_stale_jobs(conn: psycopg.Connection, expire_days: int) -> int:
    """원문 발행 N일 초과 **이고** 마지막으로 큐에 오른 지도 N일 지난
    분석 대기(PENDING·RETRY) → CANCELLED.

    운영자 재분석 요청(priority 5)과 브리프(job_type BRIEF)는 건드리지 않는다.

    available_at을 함께 보는 이유: 2차 병합·대표 교체 재분석
    (publish._merge_into_existing, clustering._add_member)과 재분석 백필
    (requeue_analysis, priority 60), 잠금 복구(queue.recover_stale_locks)는
    available_at만 now()로 되돌리고 priority·source_published_at은 그대로 둔다.
    원문 날짜만 보면 이렇게 방금 다시 큐에 오른 job이 그날 밤 정리에서 취소돼,
    게시물은 새 대표 출처를 보여주면서 사실·해석은 옛 기사 것으로 굳는다.
    """
    if expire_days <= 0:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE analysis_jobs
            SET status = 'CANCELLED',
                locked_at = NULL, locked_by = NULL,
                last_error_code = 'EXPIRED',
                last_error_message = %s
            WHERE status IN ('PENDING', 'RETRY')
              AND job_type = 'ARTICLE'
              AND priority >= 10
              AND coalesce(source_published_at, created_at)
                  < now() - make_interval(days => %s)
              AND available_at < now() - make_interval(days => %s)
            """,
            (
                f"원문 발행 {expire_days}일 초과 — 보존 정책으로 취소",
                expire_days,
                expire_days,
            ),
        )
        n = cur.rowcount
    conn.commit()
    return n


def expire_extract_backlog(conn: psycopg.Connection, expire_days: int) -> int:
    """추출 대기 만료 — collect.py와 같은 판정을 공유한다."""
    from .collect import expire_stale_pending  # 지연 import: feedparser 등 무거운 의존

    return expire_stale_pending(conn, expire_days)


def delete_dead_rows(conn: psycopg.Connection, retention_days: int) -> int:
    """EXPIRED·EXCLUDE·FAILED 원문 행 삭제 — 90일 경과, 클러스터 미소속, 수동 등록 제외.

    클러스터 구성원·대표는 게시물의 근거이므로 절대 지우지 않는다.
    """
    if retention_days <= 0:
        return 0
    return _run_batched(
        conn,
        """
        DELETE FROM raw_items
        WHERE id IN (
          SELECT ri.id FROM raw_items ri
          WHERE (ri.extract_status IN ('EXPIRED', 'FAILED')
                 OR ri.filter_status = 'EXCLUDE')
            AND ri.manual_submission_id IS NULL
            AND coalesce(ri.published_at, ri.fetched_at)
                < now() - make_interval(days => %s)
            AND NOT EXISTS (
              SELECT 1 FROM cluster_members cm WHERE cm.raw_item_id = ri.id
            )
            AND NOT EXISTS (
              SELECT 1 FROM content_clusters c
              WHERE c.representative_raw_item_id = ri.id
            )
          LIMIT %s
        )
        """,
        (retention_days,),
        "만료 행 삭제",
    )


def null_raw_response(conn: psycopg.Connection) -> int:
    """[일회성] PASS 분석의 raw_response NULL — WARN·FAIL은 추적용으로 남긴다."""
    return _run_batched(
        conn,
        """
        UPDATE analyses SET raw_response = NULL
        WHERE id IN (
          SELECT id FROM analyses
          WHERE raw_response IS NOT NULL AND validation_status = 'PASS'
          LIMIT %s
        )
        """,
        (),
        "raw_response",
    )


def delete_old_source_runs(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM source_runs WHERE started_at < now() - make_interval(days => %s)",
            (SOURCE_RUN_RETENTION_DAYS,),
        )
        n = cur.rowcount
    conn.commit()
    return n


def delete_old_gemini_calls(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM gemini_calls WHERE called_at_utc < now() - make_interval(days => %s)",
            (GEMINI_CALL_RETENTION_DAYS,),
        )
        n = cur.rowcount
    conn.commit()
    return n


def build_plan(
    conn: psycopg.Connection, settings: Settings, one_off: list[str]
) -> list[tuple[str, Callable[[], int]]]:
    """실행 순서: 일회성(요청분) → 일일 작업. 라벨은 note·로그에 그대로 쓴다."""
    plan: list[tuple[str, Callable[[], int]]] = []
    one_off_map: dict[str, tuple[str, Callable[[], int]]] = {
        "null_raw_text_all": (
            "[일회성] raw_text 전량", lambda: null_raw_text_all(conn),
        ),
        "null_raw_response": (
            "[일회성] raw_response", lambda: null_raw_response(conn),
        ),
        "expire_extract_backlog": (
            "[일회성] 추출 대기 만료",
            lambda: expire_extract_backlog(
                conn, expire_days_or_default(settings.extract_expire_days)
            ),
        ),
        "cancel_stale_jobs": (
            "[일회성] 분석 대기 만료",
            lambda: cancel_stale_jobs(
                conn, expire_days_or_default(settings.analysis_expire_days)
            ),
        ),
    }
    for name in one_off:
        plan.append(one_off_map[name])

    plan += [
        (
            f"raw_text {settings.raw_text_retention_days}일",
            lambda: null_raw_text_expired(conn, settings.raw_text_retention_days),
        ),
        ("EXCLUDE 본문", lambda: null_exclude_body(conn, settings.keep_exclude_body)),
        (
            "비대표 본문",
            lambda: null_nonrep_body(
                conn, nonrep_retention_days(settings.nonrep_body_retention_days)
            ),
        ),
        # 게시 안 된 클러스터(로봇 뉴스 아님·병합됨)의 분석 결과·대표 본문 — 금고 대상 밖
        (
            "미게시 부속",
            lambda: null_unpublished_bulk(conn, settings.unpublished_retention_days),
        ),
        (
            "분석 대기 만료",
            lambda: cancel_stale_jobs(conn, settings.analysis_expire_days),
        ),
        ("만료 행 삭제", lambda: delete_dead_rows(conn, DEAD_ROW_RETENTION_DAYS)),
        ("source_runs", lambda: delete_old_source_runs(conn)),
        ("gemini_calls", lambda: delete_old_gemini_calls(conn)),
        # 누수 차단: keep_raw_response=false인데 쓰기 경로가 안 지킨 PASS 응답 원문
        (
            "raw_response(PASS)",
            lambda: 0 if settings.keep_raw_response else null_raw_response(conn),
        ),
        # 파일 금고 — 순서 고정: 내보내기·검증 → 정리(스위치 뒤) → 자가점검
        ("금고 내보내기·검증", lambda: vault.export_and_verify(conn, settings)),
        ("금고 정리", lambda: vault.prune(conn, settings)),
        ("금고 자가점검", lambda: vault.selfcheck(conn, settings)),
    ]
    return plan


def run_plan(
    conn: psycopg.Connection, plan: list[tuple[str, Callable[[], int]]]
) -> tuple[dict[str, int], dict[str, str]]:
    """작업별로 격리 실행 — 하나가 실패해도 나머지는 계속한다."""
    counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    for label, fn in plan:
        started = time.monotonic()
        try:
            n = fn()
            counts[label] = n
            print(f"[cleanup] {label}: {n}건 ({time.monotonic() - started:.1f}초)")
        except Exception as e:  # noqa: BLE001 — 작업별 오류 격리
            conn.rollback()
            errors[label] = str(e)
            print(f"[cleanup] {label} 실패: {e}", file=sys.stderr)
    return counts, errors


def _set_usage_note(conn: psycopg.Connection, usage_id: str, note: str) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE workflow_usage SET note = %s WHERE id = %s", (note, usage_id))
    conn.commit()


# ------------------------------------------------------------
# VACUUM FULL — 사무실 PC 전용 (scripts/db_vacuum_full.bat)
# ------------------------------------------------------------
VACUUM_TABLES = ("raw_items", "analyses")


def vacuum_full(db_url: str) -> int:
    """published_items 색인 REINDEX CONCURRENTLY + VACUUM (FULL, ANALYZE)
    raw_items·analyses — 전후 DB 크기를 출력한다.

    Actions에서는 돌리지 않는다: 테이블을 통째로 잠그고(수집·분석 배치가
    기다리다 timeout) 수 분이 걸린다. 배치가 없는 시간에 사무실 PC에서 실행.
    """
    if os.environ.get("GITHUB_ACTIONS"):
        print(
            "[cleanup] VACUUM FULL은 GitHub Actions에서 실행하지 않습니다 — "
            "사무실 PC에서 scripts\\db_vacuum_full.bat을 쓰세요.",
            file=sys.stderr,
        )
        return 2
    if not db_url:
        print("[cleanup] SUPABASE_DB_URL이 없습니다 — scripts/.env를 확인하세요.", file=sys.stderr)
        return 1

    # VACUUM은 트랜잭션 안에서 못 돌린다 → autocommit 연결. db.connect의
    # statement_timeout 60초도 여기엔 너무 짧아 별도로 연다.
    conn = psycopg.connect(db_url, autocommit=True, row_factory=dict_row, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '30min'")
            cur.execute("SELECT pg_database_size(current_database()) AS bytes")
            before = int(cur.fetchone()["bytes"])
            print(f"[vacuum] 시작 전 DB 크기: {before / 1048576:.1f} MB")
            # 카드 표(published_items)는 VACUUM FULL 대상이 아니다 — 사이트가 항상
            # 읽는 표라 통째로 잠글 수 없다. 대신 색인만 무중단으로 다시 짓는다:
            # 금고 정리(vaulted_at UPDATE)나 백필처럼 카드 전량을 UPDATE 하면 색인,
            # 특히 trigram GIN이 두 배로 부푸는데 VACUUM으로는 안 줄어든다
            # (2026-09-18 실측 51→25 MB, 2026-09-22 54→36 MB). REINDEX CONCURRENTLY는
            # 새 색인을 옆에 짓고 바꿔치기하므로 읽기·쓰기를 막지 않는다.
            started = time.monotonic()
            print("[vacuum] REINDEX TABLE CONCURRENTLY published_items … (무중단)")
            try:
                cur.execute("REINDEX TABLE CONCURRENTLY published_items")
                cur.execute("VACUUM (ANALYZE) published_items")
                print(
                    f"[vacuum]   published_items 색인 재구축 완료 — "
                    f"{time.monotonic() - started:.0f}초"
                )
            except psycopg.Error as e:  # 색인 재구축이 막혀도 아래 VACUUM FULL은 진행
                print(f"[vacuum]   published_items 색인 재구축 실패: {e}", file=sys.stderr)
            cur.execute(
                """
                SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
                WHERE i.indrelid = 'public.published_items'::regclass AND NOT i.indisvalid
                """
            )
            invalid = [r["relname"] for r in cur.fetchall()]
            if invalid:
                # 중간에 끊기면 *_ccnew 색인이 invalid 로 남아 다음 REINDEX를 막는다
                print(
                    f"[vacuum]   경고: invalid 색인 {invalid} — DROP INDEX 로 지운 뒤 다시 실행",
                    file=sys.stderr,
                )
            for table in VACUUM_TABLES:
                started = time.monotonic()
                print(f"[vacuum] VACUUM (FULL, ANALYZE) {table} … (테이블 잠금, 수 분 소요)")
                cur.execute(f"VACUUM (FULL, ANALYZE) {table}")
                print(f"[vacuum]   {table} 완료 — {time.monotonic() - started:.0f}초")
            cur.execute("SELECT pg_database_size(current_database()) AS bytes")
            after = int(cur.fetchone()["bytes"])
        print(
            f"[vacuum] 완료 — DB 크기 {before / 1048576:.1f} MB → {after / 1048576:.1f} MB "
            f"(회수 {(before - after) / 1048576:.1f} MB)"
        )
    finally:
        conn.close()
    return 0


# ------------------------------------------------------------
# 진입점
# ------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KIRO 데이터 정리 배치")
    parser.add_argument(
        "--tasks",
        default=None,
        help="일회성 작업 쉼표 목록 (없으면 env CLEANUP_TASKS): " + ", ".join(ONE_OFF_TASKS),
    )
    parser.add_argument(
        "--vacuum-full",
        action="store_true",
        help="VACUUM (FULL, ANALYZE) raw_items·analyses — 사무실 PC 전용, 배치 없는 시간에",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = Settings.from_env()

    if args.vacuum_full:
        return vacuum_full(settings.supabase_db_url)

    settings.require("supabase_db_url")
    raw_tasks = args.tasks if args.tasks is not None else os.environ.get("CLEANUP_TASKS")
    one_off, unknown = parse_tasks(raw_tasks)
    if unknown:
        print(f"[cleanup] 모르는 일회성 작업 이름(무시): {', '.join(unknown)}", file=sys.stderr)

    conn = connect(settings.supabase_db_url)
    settings.apply_app_settings(load_app_settings(conn))
    usage = UsageRecorder(
        conn, "cleanup", note=("일회성: " + ", ".join(one_off)) if one_off else None,
    )
    print(
        "[cleanup] 보존 정책 — "
        f"raw_text {settings.raw_text_retention_days}일, "
        f"추출 만료 {settings.extract_expire_days}일, "
        f"분석 만료 {settings.analysis_expire_days}일, "
        f"비대표 본문 {nonrep_retention_days(settings.nonrep_body_retention_days)}일, "
        f"미게시 부속 {settings.unpublished_retention_days}일, "
        f"EXCLUDE 본문 보존 {settings.keep_exclude_body}, "
        f"raw_response 보존 {settings.keep_raw_response}, "
        f"금고 창 {vault.effective_window_days(settings.vault_window_days)}일, "
        f"금고 정리 {'켜짐' if settings.vault_prune_enabled else '꺼짐'}"
        f"(상한 {settings.vault_prune_max_per_run}건)"
        + (f" · 일회성 {one_off}" if one_off else "")
    )

    counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    try:
        counts, errors = run_plan(conn, build_plan(conn, settings, one_off))
        try:
            with conn.cursor() as cur:
                cur.execute("ANALYZE")
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            errors["ANALYZE"] = str(e)
            print(f"[cleanup] ANALYZE 실패: {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        errors["전체"] = str(e)
        print(f"[cleanup] 실패: {e}", file=sys.stderr)
    finally:
        note = usage_note(counts, errors)
        print(f"[cleanup] 요약: {note}")
        try:
            _set_usage_note(conn, usage.usage_id, note)
        except Exception as e:  # noqa: BLE001 — 기록 실패가 정리 결과를 바꾸면 안 된다
            conn.rollback()
            print(f"[cleanup] note 기록 실패: {e}", file=sys.stderr)
        status = run_status(counts, errors)
        usage.finish(status)
        conn.close()
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
