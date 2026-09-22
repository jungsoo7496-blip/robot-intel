"""데이터 보존 정책 — 순수 판정 함수 테스트 (DB 없이, 2026-09-08).

cleanup.py의 작업 선택·일수 판정, collect.py의 저장 판정·대기 정렬키,
publish.py의 raw_response 저장 판정, config.py의 불리언 설정 해석을 고정한다.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from kiro_batch import cleanup
from kiro_batch.cleanup import (
    DEFAULT_EXPIRE_DAYS,
    ONE_OFF_TASKS,
    expire_days_or_default,
    nonrep_retention_days,
    parse_args,
    parse_tasks,
    run_status,
    usage_note,
)
from kiro_batch.collect import clean_text_to_store, pending_sort_key
from kiro_batch.config import Settings, parse_bool
from kiro_batch.publish import (
    MERGE_WINDOW_DAYS,
    RAW_RESPONSE_MAX_CHARS,
    raw_response_to_store,
)

UTC = timezone.utc


# ------------------------------------------------------------
# cleanup.parse_tasks — CLEANUP_TASKS(workflow_dispatch input) 해석
# ------------------------------------------------------------
class TestParseTasks:
    def test_empty_means_daily_only(self):
        assert parse_tasks(None) == ([], [])
        assert parse_tasks("") == ([], [])
        assert parse_tasks(" , ") == ([], [])

    def test_screen_presets(self):
        # 운영 화면 버튼이 보내는 두 조합 그대로
        assert parse_tasks("null_raw_text_all,null_raw_response") == (
            ["null_raw_text_all", "null_raw_response"], [],
        )
        assert parse_tasks("expire_extract_backlog,cancel_stale_jobs") == (
            ["expire_extract_backlog", "cancel_stale_jobs"], [],
        )

    def test_whitespace_case_and_duplicates(self):
        valid, unknown = parse_tasks(" Null_Raw_Text_All , null_raw_text_all ,cancel_stale_jobs")
        assert valid == ["null_raw_text_all", "cancel_stale_jobs"]
        assert unknown == []

    def test_unknown_names_are_reported_not_run(self):
        valid, unknown = parse_tasks("null_raw_response,drop_everything,vacuum")
        assert valid == ["null_raw_response"]
        assert unknown == ["drop_everything", "vacuum"]

    def test_every_declared_task_parses(self):
        valid, unknown = parse_tasks(",".join(ONE_OFF_TASKS))
        assert valid == list(ONE_OFF_TASKS) and unknown == []


# ------------------------------------------------------------
# 일수 판정
# ------------------------------------------------------------
class TestRetentionDays:
    def test_nonrep_zero_disables(self):
        assert nonrep_retention_days(0) == 0
        assert nonrep_retention_days(-3) == 0

    def test_nonrep_never_shorter_than_merge_window(self):
        # 2차 병합 창 안에서는 비대표가 대표로 승격될 수 있다 — 창 길이로 올린다
        assert MERGE_WINDOW_DAYS == 7
        assert nonrep_retention_days(3) == MERGE_WINDOW_DAYS
        assert nonrep_retention_days(7) == 7
        assert nonrep_retention_days(30) == 30

    def test_nonrep_follows_merge_window_constant(self):
        # publish.MERGE_WINDOW_DAYS가 바뀌면 같이 움직여야 한다
        assert nonrep_retention_days(3, merge_window_days=10) == 10

    def test_one_off_expire_uses_default_when_disabled(self):
        assert DEFAULT_EXPIRE_DAYS == 3
        assert expire_days_or_default(0) == 3
        assert expire_days_or_default(-1) == 3
        assert expire_days_or_default(5) == 5
        assert expire_days_or_default(0, default=9) == 9


# ------------------------------------------------------------
# workflow_usage 기록
# ------------------------------------------------------------
class TestUsageSummary:
    def test_note_lists_counts_and_failures(self):
        note = usage_note({"raw_text 30일": 12, "EXCLUDE 본문": 340}, {"만료 행 삭제": "timeout"})
        assert "raw_text 30일 12건" in note
        assert "EXCLUDE 본문 340건" in note
        assert "만료 행 삭제 실패(timeout)" in note

    def test_note_when_nothing_ran(self):
        assert usage_note({}, {}) == "할 일 없음"

    def test_note_is_bounded(self):
        counts = {f"작업{i}": i for i in range(200)}
        assert len(usage_note(counts, {})) <= 900

    def test_status(self):
        assert run_status({"a": 1}, {}) == "SUCCESS"
        assert run_status({"a": 1}, {"b": "x"}) == "PARTIAL"
        assert run_status({}, {"b": "x"}) == "FAILURE"


class TestCli:
    def test_defaults(self):
        args = parse_args([])
        assert args.tasks is None and args.vacuum_full is False

    def test_tasks_and_vacuum_flags(self):
        args = parse_args(["--tasks", "null_raw_text_all,null_raw_response", "--vacuum-full"])
        assert args.tasks == "null_raw_text_all,null_raw_response"
        assert args.vacuum_full is True

    def test_vacuum_full_refuses_in_actions(self, monkeypatch):
        # Actions 시간·테이블 잠금 — 사무실 PC 전용. DB에 닿기 전에 거부해야 한다
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        assert cleanup.vacuum_full("postgresql://x") == 2

    def test_vacuum_full_needs_db_url(self, monkeypatch):
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        assert cleanup.vacuum_full("") == 1


# ------------------------------------------------------------
# collect.clean_text_to_store — EXCLUDE 본문 저장 중단
# ------------------------------------------------------------
class TestCleanTextToStore:
    def test_empty_body_is_null(self):
        assert clean_text_to_store(None, "PASS", False, 100) is None
        assert clean_text_to_store("", "PASS", False, 100) is None

    def test_exclude_dropped_unless_kept(self):
        assert clean_text_to_store("본문", "EXCLUDE", False, 100) is None
        assert clean_text_to_store("본문", "EXCLUDE", True, 100) == "본문"

    def test_pass_and_low_priority_kept_and_truncated(self):
        assert clean_text_to_store("가나다라마", "PASS", False, 3) == "가나다"
        assert clean_text_to_store("가나다", "LOW_PRIORITY", False, 100) == "가나다"


# ------------------------------------------------------------
# collect.pending_sort_key — korea.kr(published_at NULL) 기아 수리
# ------------------------------------------------------------
class TestPendingSortKey:
    def test_published_at_preferred(self):
        pub = datetime(2026, 9, 7, 9, tzinfo=UTC)
        fetched = datetime(2026, 9, 8, 9, tzinfo=UTC)
        assert pending_sort_key({"published_at": pub, "fetched_at": fetched}) == pub

    def test_fetched_at_fallback_when_published_missing(self):
        fetched = datetime(2026, 9, 8, 9, tzinfo=UTC)
        assert pending_sort_key({"published_at": None, "fetched_at": fetched}) == fetched

    def test_null_published_item_is_not_pushed_to_the_end(self):
        now = datetime(2026, 9, 8, 9, tzinfo=UTC)
        items = [
            {"id": "old", "published_at": now - timedelta(days=5), "fetched_at": now - timedelta(days=5)},
            {"id": "korea_kr", "published_at": None, "fetched_at": now - timedelta(hours=1)},
            {"id": "fresh", "published_at": now - timedelta(hours=2), "fetched_at": now},
        ]
        items.sort(key=pending_sort_key, reverse=True)
        # 종전(datetime.min)에는 korea_kr이 항상 맨 뒤라 예산에 잘렸다
        assert [i["id"] for i in items] == ["korea_kr", "fresh", "old"]

    def test_missing_both_sorts_last(self):
        assert pending_sort_key({}) == datetime.min.replace(tzinfo=UTC)


# ------------------------------------------------------------
# publish.raw_response_to_store — Gemini 응답 원문 저장 중단
# ------------------------------------------------------------
class TestRawResponseToStore:
    def test_pass_dropped_when_not_kept(self):
        assert raw_response_to_store("{...}", "PASS", False) is None

    def test_non_pass_always_kept_for_debugging(self):
        for status in ("WARN", "FAIL", "PENDING"):
            stored = raw_response_to_store("응답 원문", status, False)
            assert stored is not None
            assert json.loads(stored) == {"text": "응답 원문"}

    def test_keep_flag_stores_pass(self):
        stored = raw_response_to_store("한글 응답", "PASS", True)
        assert json.loads(stored) == {"text": "한글 응답"}
        assert "한글" in stored  # ensure_ascii=False 유지

    def test_truncated_to_existing_cap(self):
        assert RAW_RESPONSE_MAX_CHARS == 20000
        stored = raw_response_to_store("x" * 30000, "WARN", False)
        assert len(json.loads(stored)["text"]) == RAW_RESPONSE_MAX_CHARS


# ------------------------------------------------------------
# config — 불리언 설정 해석과 우선순위 (환경변수 > app_settings > 기본값)
# ------------------------------------------------------------
class TestRetentionSettings:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            (True, True), (False, False),
            ("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
            ("false", False), ("0", False), ("", False), ("off", False), ("no", False),
            (1, True), (0, False),
        ],
    )
    def test_parse_bool(self, raw, expected):
        assert parse_bool(raw) is expected

    def test_defaults(self):
        s = Settings()
        assert s.extract_expire_days == 3
        assert s.analysis_expire_days == 3
        assert s.nonrep_body_retention_days == 7
        assert s.keep_exclude_body is False
        assert s.keep_raw_response is False

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("EXTRACT_EXPIRE_DAYS", "5")
        monkeypatch.setenv("KEEP_RAW_RESPONSE", "true")
        monkeypatch.setenv("KEEP_EXCLUDE_BODY", "false")  # int("false")로 죽으면 안 된다
        s = Settings.from_env()
        assert s.extract_expire_days == 5
        assert s.keep_raw_response is True
        assert s.keep_exclude_body is False

    def test_app_settings_jsonb_values(self, monkeypatch):
        for key in ("EXTRACT_EXPIRE_DAYS", "ANALYSIS_EXPIRE_DAYS",
                    "NONREP_BODY_RETENTION_DAYS", "KEEP_EXCLUDE_BODY", "KEEP_RAW_RESPONSE"):
            monkeypatch.delenv(key, raising=False)
        s = Settings()
        # psycopg는 jsonb를 파이썬 값으로 준다 (bool/int). 문자열로 와도 json.loads 경로
        s.apply_app_settings({
            "extract_expire_days": 0,
            "analysis_expire_days": "5",
            "nonrep_body_retention_days": 14,
            "keep_exclude_body": True,
            "keep_raw_response": "false",
        })
        assert s.extract_expire_days == 0
        assert s.analysis_expire_days == 5
        assert s.nonrep_body_retention_days == 14
        assert s.keep_exclude_body is True
        assert s.keep_raw_response is False

    def test_env_wins_over_app_settings(self, monkeypatch):
        monkeypatch.setenv("KEEP_EXCLUDE_BODY", "true")
        s = Settings.from_env()
        s.apply_app_settings({"keep_exclude_body": False})
        assert s.keep_exclude_body is True


# ------------------------------------------------------------
# cancel_stale_jobs — 취소 술어 (DB 없이 SQL만 검사)
# ------------------------------------------------------------
class _SqlCaptureCursor:
    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sink.append((sql, params))

    @property
    def rowcount(self):
        return 0


class _SqlCaptureConn:
    """실행된 SQL만 모으는 가짜 커넥션."""

    def __init__(self):
        self.executed: list[tuple[str, object]] = []
        self.commits = 0

    def cursor(self):
        return _SqlCaptureCursor(self.executed)

    def commit(self):
        self.commits += 1


class TestCancelStaleJobs:
    def test_zero_days_never_touches_db(self):
        conn = _SqlCaptureConn()
        assert cleanup.cancel_stale_jobs(conn, 0) == 0
        assert cleanup.cancel_stale_jobs(conn, -1) == 0
        assert conn.executed == []

    def test_requires_both_article_age_and_requeue_age(self):
        # 대표 교체 재분석·백필(requeue_analysis)·잠금 복구는 available_at만 now()로
        # 올리므로, 이 조건이 없으면 방금 다시 큐에 오른 job이 그날 밤 취소된다
        conn = _SqlCaptureConn()
        cleanup.cancel_stale_jobs(conn, 3)
        sql, params = conn.executed[0]
        assert "coalesce(source_published_at, created_at)" in sql
        assert "available_at < now() - make_interval(days => %s)" in sql
        # 두 조건 모두 같은 보존 일수를 쓴다 (메시지 + 일수 2개)
        assert params[1] == 3 and params[2] == 3

    def test_operator_reanalysis_and_briefs_excluded(self):
        conn = _SqlCaptureConn()
        cleanup.cancel_stale_jobs(conn, 3)
        sql, _ = conn.executed[0]
        assert "job_type = 'ARTICLE'" in sql  # 브리프 job 제외
        assert "priority >= 10" in sql  # 운영 화면 재분석(priority 5) 제외


# ------------------------------------------------------------
# null_unpublished_bulk — 게시 카드 없는 클러스터의 부속 비우기 (2026-09-22)
# 금고는 게시 기사만 담으므로 로봇 뉴스 아님·병합됨 클러스터의 analyses·대표 본문은
# 여기서 비운다. 기사 행(제목·링크)·클러스터·구성원은 남긴다 — 운영자 제약.
# ------------------------------------------------------------
class TestNullUnpublishedBulk:
    def test_zero_days_never_touches_db(self):
        conn = _SqlCaptureConn()
        assert cleanup.null_unpublished_bulk(conn, 0) == 0
        assert cleanup.null_unpublished_bulk(conn, -1) == 0
        assert conn.executed == []

    def test_deletes_analyses_then_nulls_representative_body(self):
        conn = _SqlCaptureConn()
        cleanup.null_unpublished_bulk(conn, 30)
        assert len(conn.executed) == 2
        (del_sql, del_params), (upd_sql, upd_params) = conn.executed
        assert del_sql.strip().startswith("DELETE FROM analyses")
        assert upd_sql.strip().startswith(
            "UPDATE raw_items SET clean_text = NULL, raw_text = NULL"
        )
        # 보존 일수가 첫 매개변수, 배치 상한이 마지막 매개변수 (_run_batched 규약)
        assert del_params[0] == 30 and del_params[-1] == cleanup.BATCH_ROWS
        assert upd_params[0] == 30 and upd_params[-1] == cleanup.BATCH_ROWS

    def test_only_old_unpublished_clusters_without_live_jobs(self):
        conn = _SqlCaptureConn()
        cleanup.null_unpublished_bulk(conn, 30)
        for sql, params in conn.executed:
            assert "c.created_at < now() - make_interval(days => %s)" in sql
            assert (
                "NOT EXISTS (SELECT 1 FROM published_items p WHERE p.cluster_id = c.id)"
                in sql
            )
            assert "j.status = ANY(%s)" in sql
            assert list(cleanup.ACTIVE_JOB_STATUSES) in params

    def test_analysis_a_card_points_at_is_never_deleted(self):
        # FK는 ON DELETE SET NULL이라 오류 없이 카드 본문만 사라진다 — SQL에서 막는다
        conn = _SqlCaptureConn()
        cleanup.null_unpublished_bulk(conn, 30)
        del_sql, _ = conn.executed[0]
        assert "p.current_analysis_id = a.id" in del_sql

    def test_representative_body_protected_like_vault_c5(self):
        conn = _SqlCaptureConn()
        cleanup.null_unpublished_bulk(conn, 30)
        upd_sql, upd_params = conn.executed[1]
        # 같은 raw_item을 대표로 둔 다른 클러스터가 젊거나·미금고 카드가 있거나·job이 살아 있으면 보호
        assert "c2.representative_raw_item_id = ri.id AND c2.id <> c.id" in upd_sql
        assert "c2.created_at >= now() - make_interval(days => %s)" in upd_sql
        assert "p2.vaulted_at IS NULL" in upd_sql
        # 구성원으로 속한 클러스터(병합 대상 포함)에 살아 있는 job이 있으면 보호
        assert "cm3.raw_item_id = ri.id AND j3.status = ANY(%s)" in upd_sql
        # 자기 클러스터·다른 클러스터 나이 판정에 같은 일수를 쓴다
        assert [p for p in upd_params if p == 30] == [30, 30]

    def test_article_rows_clusters_and_members_are_kept(self):
        conn = _SqlCaptureConn()
        cleanup.null_unpublished_bulk(conn, 30)
        joined = " ".join(sql for sql, _ in conn.executed)
        assert "DELETE FROM raw_items" not in joined
        assert "DELETE FROM content_clusters" not in joined
        assert "DELETE FROM cluster_members" not in joined

    def test_daily_plan_runs_it_after_nonrep_body_and_before_vault(self):
        conn = _SqlCaptureConn()
        labels = [label for label, _ in cleanup.build_plan(conn, Settings(), [])]
        assert labels.index("미게시 부속") == labels.index("비대표 본문") + 1
        assert labels.index("미게시 부속") < labels.index("금고 내보내기·검증")

    def test_setting_default_env_and_app_settings(self, monkeypatch):
        monkeypatch.delenv("UNPUBLISHED_RETENTION_DAYS", raising=False)
        assert Settings().unpublished_retention_days == 30
        s = Settings()
        s.apply_app_settings({"unpublished_retention_days": "45"})
        assert s.unpublished_retention_days == 45
        monkeypatch.setenv("UNPUBLISHED_RETENTION_DAYS", "0")
        assert Settings.from_env().unpublished_retention_days == 0
