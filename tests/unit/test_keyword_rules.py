"""keyword_rules 로더·매처 단위 테스트 (DB 없이 — 가짜 커넥션).

배치가 어떤 경우에도 멈추지 않아야 한다: DB 실패·0건 → 기본값 폴백.
"""

from __future__ import annotations

import pytest

from kiro_batch import collect_rnd
from kiro_batch import keyword_rules as kr
from kiro_batch.keyword_rules import (
    Term,
    compile_terms,
    load_rules,
    load_setting_int,
)
from kiro_batch.reports import classify


class FakeCursor:
    def __init__(self, rows, error):
        self._rows = rows
        self._error = error
        self.sql = ""
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if self._error is not None:
            raise self._error
        self.sql = sql
        self.params = params

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """dict_row 커넥션 흉내. rows는 모든 쿼리에 같은 답을 준다."""

    def __init__(self, rows=None, error: Exception | None = None):
        self.rows = rows or []
        self.error = error
        self.queries = 0
        self.rollbacks = 0
        self.last_cursor: FakeCursor | None = None

    def cursor(self):
        self.queries += 1
        self.last_cursor = FakeCursor(self.rows, self.error)
        return self.last_cursor

    def rollback(self):
        self.rollbacks += 1


def row(kind: str, term: str, is_regex: bool = False) -> dict:
    return {"kind": kind, "term": term, "is_regex": is_regex}


@pytest.fixture(autouse=True)
def _isolate_module_state():
    """프로세스 캐시·모듈 매처는 전역이라 테스트마다 초기화한다."""
    kr.clear_cache()
    classify.reset()
    collect_rnd.reset()
    yield
    kr.clear_cache()
    classify.reset()
    collect_rnd.reset()


# ------------------------------------------------------------
# load_rules
# ------------------------------------------------------------
class TestLoadRules:
    def test_groups_by_kind_and_keeps_regex_flag(self):
        conn = FakeConn([
            row("robot", "로봇"),
            row("robot", "드론"),
            row("exclude", r"\[채용\]", True),
        ])
        rules = load_rules(conn, "news_filter")
        assert rules == {
            "robot": [("로봇", False), ("드론", False)],
            "exclude": [(r"\[채용\]", True)],
        }
        assert conn.last_cursor.params == ("news_filter",)
        assert "enabled" in conn.last_cursor.sql

    def test_blank_terms_are_dropped(self):
        conn = FakeConn([row("robot", "  "), row("robot", " 로봇 ")])
        assert load_rules(conn, "news_filter") == {"robot": [("로봇", False)]}

    def test_second_call_uses_cache(self):
        conn = FakeConn([row("core", "로봇")])
        assert load_rules(conn, "report_tier") == {"core": [("로봇", False)]}
        conn.rows = [row("core", "바뀜")]
        assert load_rules(conn, "report_tier") == {"core": [("로봇", False)]}
        assert conn.queries == 1

    def test_empty_result_is_empty_dict(self):
        assert load_rules(FakeConn([]), "rnd_keywords") == {}

    def test_db_error_returns_empty_rolls_back_and_is_not_cached(self):
        conn = FakeConn(error=RuntimeError("connection lost"))
        assert load_rules(conn, "news_filter") == {}
        assert conn.rollbacks == 1
        # 실패는 캐시하지 않는다 — 다시 시도한다
        conn.error = None
        conn.rows = [row("robot", "로봇")]
        assert load_rules(conn, "news_filter") == {"robot": [("로봇", False)]}
        assert conn.queries == 2

    def test_unknown_rule_set_raises(self):
        with pytest.raises(ValueError):
            load_rules(FakeConn(), "nope")


# ------------------------------------------------------------
# load_setting_int
# ------------------------------------------------------------
class TestLoadSettingInt:
    def test_reads_jsonb_number(self):
        assert load_setting_int(FakeConn([{"value": 250}]), "k", 200) == 250

    def test_reads_json_string(self):
        assert load_setting_int(FakeConn([{"value": "300"}]), "k", 200) == 300

    def test_missing_row_uses_default(self):
        assert load_setting_int(FakeConn([]), "k", 200) == 200

    def test_non_integer_uses_default(self):
        assert load_setting_int(FakeConn([{"value": "abc"}]), "k", 200) == 200

    def test_db_error_uses_default_and_rolls_back(self):
        conn = FakeConn(error=RuntimeError("boom"))
        assert load_setting_int(conn, "k", 200) == 200
        assert conn.rollbacks == 1


# ------------------------------------------------------------
# Term / compile_terms
# ------------------------------------------------------------
class TestTerms:
    def test_plain_term_is_case_insensitive_substring(self):
        (t,) = compile_terms([("Robot", False)])
        assert t.find("industrial robot arm") == "robot"
        assert t.find("로봇") is None

    def test_regex_term_returns_actual_match(self):
        (t,) = compile_terms([(r"(?<![a-z0-9])ai(?![a-z0-9])", True)])
        assert t.find("edge ai 팩토리") == "ai"
        assert t.find("supply chain") is None
        assert t.is_regex and isinstance(t, Term)

    def test_invalid_regex_is_skipped_with_warning(self, capsys):
        terms = compile_terms([("(", True), ("로봇", False)], label="test")
        assert [t.term for t in terms] == ["로봇"]
        assert "정규식 컴파일 실패" in capsys.readouterr().err

    def test_blank_terms_are_skipped(self):
        assert compile_terms([("", False), ("  ", True)]) == []

    @pytest.mark.parametrize(
        "pattern",
        [r"(a+)+$", r"(\w+\s?)*$", r"(.*a){20}", r"((a+)x)+", r"(로봇\s*)+"],
    )
    def test_nested_quantifier_is_detected(self, pattern):
        assert kr.has_nested_quantifier(pattern) is True

    @pytest.mark.parametrize(
        "pattern",
        [
            r"\[채용\]",
            r"AD\b",
            r"(?<![a-z0-9])ai(?![a-z0-9])",
            r"(?:로봇|드론)*",
            r"([+])+",  # [...] 안의 +는 반복이 아니라 글자
        ],
    )
    def test_seed_patterns_are_not_flagged(self, pattern):
        """시드 규칙(마이그레이션 20260908000023)이 오탐으로 빠지면 안 된다."""
        assert kr.has_nested_quantifier(pattern) is False
        assert len(compile_terms([(pattern, True)])) == 1

    def test_nested_quantifier_regex_is_skipped_with_warning(self, capsys):
        terms = compile_terms([(r"(a+)+$", True), ("로봇", False)], label="test")
        assert [t.term for t in terms] == ["로봇"]
        assert "중첩 반복" in capsys.readouterr().err

    def test_slow_regex_times_out_and_is_dropped_for_the_run(self, monkeypatch, capsys):
        """중첩 반복 검사를 빠져나간 폭주 정규식은 시간 제한이 끊는다."""
        if kr._regex_module is None:  # pragma: no cover — regex 없는 환경
            pytest.skip("regex 모듈이 없어 시간 제한을 쓸 수 없다")
        monkeypatch.setattr(kr, "REGEX_TIMEOUT_SECONDS", 0.05)
        (t,) = compile_terms([("(a|a)*$", True)], label="test")
        text = "a" * 40 + "!"
        assert t.find(text) is None
        assert "0.05초를 넘겨" in capsys.readouterr().err
        # 남은 기사마다 다시 시도하지 않는다 (경고도 한 번뿐)
        assert t.find(text) is None
        assert capsys.readouterr().err == ""


# ------------------------------------------------------------
# reports.classify — 모듈 상수 기본값 ↔ DB 규칙 교체
# ------------------------------------------------------------
class TestClassifyConfigure:
    def test_defaults_before_configure(self):
        assert classify.keyword_tier("휴머노이드 로봇 동향") == "CORE"
        assert classify.keyword_tier("드론 산업 동향") == "TOOL"

    def test_configure_replaces_both_tiers(self):
        assert classify.configure({
            "core": [("잠수함", False)],
            "tool": [("(?<![a-z0-9])xr(?![a-z0-9])", True)],
        }) is True
        assert classify.keyword_tier("잠수함 개발 현황") == "CORE"
        assert classify.keyword_tier("XR 장비 보고서") == "TOOL"
        # 기본값의 '로봇'은 더 이상 규칙이 아니다
        assert classify.keyword_tier("로봇 산업 전망") is None
        assert classify.matched_keywords("잠수함 XR") == ["xr", "잠수함"]

    def test_configure_without_core_falls_back_to_defaults(self):
        classify.configure({"core": [("잠수함", False)]})
        assert classify.configure({"tool": [("드론", False)]}) is False
        assert classify.keyword_tier("로봇 산업 전망") == "CORE"

    def test_configure_from_db(self):
        conn = FakeConn([row("core", "잠수함"), row("tool", "드론")])
        assert classify.configure_from_db(conn) is True
        assert classify.keyword_tier("잠수함") == "CORE"
        assert classify.keyword_tier("드론") == "TOOL"
        assert classify.keyword_tier("로봇") is None

    def test_configure_from_db_error_keeps_defaults(self):
        conn = FakeConn(error=RuntimeError("down"))
        assert classify.configure_from_db(conn) is False
        assert classify.keyword_tier("로봇 산업 전망") == "CORE"

    def test_reset_restores_defaults(self):
        classify.configure({"core": [("잠수함", False)]})
        classify.reset()
        assert classify.keyword_tier("로봇") == "CORE"


# ------------------------------------------------------------
# collect_rnd — ROBOT_KEYWORDS + 패턴 ↔ DB 규칙 교체
# ------------------------------------------------------------
class TestRndConfigure:
    def test_defaults_before_configure(self):
        assert collect_rnd.match_keywords("AI기반 로봇 자율 제조", None) == [
            "ai", "로봇", "자율",
        ]

    def test_configure_replaces_keywords_and_patterns(self):
        assert collect_rnd.configure({
            "keyword": [("바이오", False)],
            "pattern": [("(?<![a-z0-9])xr(?![a-z0-9])", True)],
        }) is True
        assert collect_rnd.match_keywords("바이오 XR 장비", None) == ["xr", "바이오"]
        assert collect_rnd.match_keywords("로봇 자동화", None) == []

    def test_configure_without_keyword_falls_back(self):
        assert collect_rnd.configure({"pattern": [("xr", True)]}) is False
        assert "로봇" in collect_rnd.match_keywords("로봇", None)

    def test_configure_from_db(self):
        conn = FakeConn([row("keyword", "바이오"), row("pattern", "xr", True)])
        assert collect_rnd.configure_from_db(conn) is True
        assert collect_rnd.match_keywords("바이오 xr", None) == ["xr", "바이오"]

    def test_configure_from_db_error_keeps_defaults(self):
        assert collect_rnd.configure_from_db(FakeConn(error=RuntimeError("x"))) is False
        assert "로봇" in collect_rnd.match_keywords("로봇", None)
