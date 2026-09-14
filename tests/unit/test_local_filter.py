"""로컬 1차 필터 단위 테스트 (FR-004, tasks §18.1).

JSON 기본값 경로(LocalFilter()) + 규칙 dict 주입(from_rules) + DB 경로 폴백
(from_db, 가짜 커넥션 — 실제 DB 없이 실행).
"""

import pytest

from kiro_batch import keyword_rules as kr
from kiro_batch.local_filter import LocalFilter

LONG_ROBOT_BODY = (
    "산업통상자원부는 휴머노이드 로봇 실증 사업을 시작한다고 밝혔다. "
    "이번 사업은 제조 현장에 로봇을 투입해 피지컬 AI 기술을 검증하는 것이 목표다. "
) * 10


def make_filter() -> LocalFilter:
    return LocalFilter()


def test_robot_article_passes():
    f = make_filter()
    result = f.evaluate("휴머노이드 로봇 실증 사업 착수", LONG_ROBOT_BODY)
    assert result.status == "PASS"


def test_unrelated_article_excluded():
    f = make_filter()
    result = f.evaluate(
        "부동산 시장 전망", "서울 아파트 가격이 상승세를 이어가고 있다. " * 30
    )
    assert result.status == "EXCLUDE"


def test_recruiting_excluded():
    f = make_filter()
    result = f.evaluate("[채용] 로봇 엔지니어 신입사원 모집", LONG_ROBOT_BODY)
    assert result.status == "EXCLUDE"


def test_stock_only_excluded():
    f = make_filter()
    result = f.evaluate("로봇 관련주 총정리…주가 급등", LONG_ROBOT_BODY)
    assert result.status == "EXCLUDE"


def test_short_body_low_priority():
    f = make_filter()
    result = f.evaluate("휴머노이드 로봇 발표", "짧은 본문")
    assert result.status == "LOW_PRIORITY"


def test_event_only_low_priority():
    f = make_filter()
    result = f.evaluate("로봇 전시회 참가자 모집", LONG_ROBOT_BODY)
    assert result.status == "LOW_PRIORITY"


def test_uncertain_not_discarded():
    """불확실한 콘텐츠는 EXCLUDE가 아니라 LOW_PRIORITY로 보존 (FR-004)."""
    f = make_filter()
    body = "제조 공정 자동화 뉴스입니다. 현장에는 로봇 한 대가 있다. " + "일반 내용. " * 60
    result = f.evaluate("공장 자동화 소식", body)
    assert result.status in ("PASS", "LOW_PRIORITY")


# ------------------------------------------------------------
# 규칙 dict 주입 (from_rules) — DB 규칙과 같은 {kind: [(term, is_regex)]}
# ------------------------------------------------------------
SUB_BODY = "해군이 신형 잠수함을 공개했다. 잠수함은 무인 운용이 가능하다. " * 10


def test_from_rules_uses_injected_terms_only():
    f = LocalFilter.from_rules({
        "robot": [("잠수함", False)],
        "strong": [],
        "exclude": [(r"\[광고\]", True)],
        "event_only": [("시승 행사", True)],
    }, min_body_length=50)
    assert f.source == "rules"
    assert f.evaluate("신형 잠수함 공개", SUB_BODY).status == "PASS"
    # 기본값의 '로봇'은 더 이상 키워드가 아니다
    assert f.evaluate("로봇 신제품", LONG_ROBOT_BODY).status == "EXCLUDE"
    assert f.evaluate("[광고] 잠수함 특가", SUB_BODY).status == "EXCLUDE"
    assert f.evaluate("잠수함 시승 행사 개최", SUB_BODY).status == "LOW_PRIORITY"


def test_from_rules_missing_kinds_are_empty():
    f = LocalFilter.from_rules({"robot": [("잠수함", False)]})
    assert f.exclude_terms == [] and f.event_only_terms == []
    assert f.min_body_length == 200
    assert f.evaluate("잠수함", "짧음").status == "LOW_PRIORITY"


def test_from_rules_regex_robot_term():
    f = LocalFilter.from_rules({
        "robot": [(r"(?<![a-z0-9])ai(?![a-z0-9])", True)],
    }, min_body_length=10)
    body = "AI 기반 공정 지능화 사례를 소개한다. " * 5
    assert f.evaluate("AI기반 공장", body).status == "PASS"
    # 'chain'·'maintain' 속 ai는 단어가 아니다
    assert f.evaluate("Supply chain", "maintain the chain " * 5).status == "EXCLUDE"


def test_from_rules_invalid_regex_is_skipped(capsys):
    f = LocalFilter.from_rules({
        "robot": [("로봇", False)],
        "exclude": [("(", True), (r"\[채용\]", True)],
    })
    assert [t.term for t in f.exclude_terms] == [r"\[채용\]"]
    assert "정규식 컴파일 실패" in capsys.readouterr().err
    assert f.evaluate("[채용] 로봇 엔지니어", LONG_ROBOT_BODY).status == "EXCLUDE"
    assert f.evaluate("휴머노이드 로봇 실증", LONG_ROBOT_BODY).status == "PASS"


# ------------------------------------------------------------
# DB 경로 (from_db) — 가짜 커넥션. 실패·0건이면 JSON 기본값으로 폴백
# ------------------------------------------------------------
class _Cursor:
    def __init__(self, conn):
        self._conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if self._conn.error is not None:
            raise self._conn.error
        if "FROM app_settings" in sql:
            self._rows = self._conn.settings_rows
        elif "FROM keyword_rules" in sql:
            self._rows = self._conn.rule_rows
        else:  # pragma: no cover
            raise AssertionError(f"예상 밖 쿼리: {sql}")

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, rule_rows=None, settings_rows=None, error=None):
        self.rule_rows = rule_rows or []
        self.settings_rows = settings_rows or []
        self.error = error
        self.rollbacks = 0

    def cursor(self):
        return _Cursor(self)

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture(autouse=True)
def _clear_rule_cache():
    kr.clear_cache()
    yield
    kr.clear_cache()


def test_from_db_uses_db_rules_and_setting():
    conn = _Conn(
        rule_rows=[
            {"kind": "robot", "term": "잠수함", "is_regex": False},
            {"kind": "exclude", "term": r"\[광고\]", "is_regex": True},
        ],
        settings_rows=[{"value": 30}],
    )
    f = LocalFilter.from_db(conn)
    assert f.source == "DB"
    assert f.min_body_length == 30
    assert [t.term for t in f.robot_terms] == ["잠수함"]
    assert f.evaluate("신형 잠수함 공개", SUB_BODY).status == "PASS"
    assert f.evaluate("로봇 신제품", LONG_ROBOT_BODY).status == "EXCLUDE"


def test_from_db_falls_back_to_json_when_empty(capsys):
    f = LocalFilter.from_db(_Conn(rule_rows=[]))
    assert f.source == "JSON"
    assert f.min_body_length == 200
    assert f.evaluate("휴머노이드 로봇 실증 사업 착수", LONG_ROBOT_BODY).status == "PASS"
    assert "기본값" in capsys.readouterr().err


def test_from_db_falls_back_to_json_on_db_error():
    conn = _Conn(error=RuntimeError("connection refused"))
    f = LocalFilter.from_db(conn)
    assert f.source == "JSON"
    assert conn.rollbacks >= 1
    assert f.evaluate("[채용] 로봇 엔지니어 신입사원 모집", LONG_ROBOT_BODY).status == "EXCLUDE"
    assert f.evaluate("휴머노이드 로봇 실증 사업 착수", LONG_ROBOT_BODY).status == "PASS"


def test_from_db_without_robot_kind_falls_back_entirely():
    # exclude만 있고 robot이 없으면 전부 제외돼 버리므로 JSON 전체로 폴백
    conn = _Conn(rule_rows=[{"kind": "exclude", "term": "x", "is_regex": False}])
    f = LocalFilter.from_db(conn)
    assert f.source == "JSON"
    assert f.evaluate("휴머노이드 로봇 실증 사업 착수", LONG_ROBOT_BODY).status == "PASS"


def test_from_db_setting_changes_short_body_verdict():
    rows = [{"kind": "robot", "term": "로봇", "is_regex": False}]
    short = "로봇 한 줄 요약입니다. 이 문장은 오십 자를 조금 넘습니다. 그래서 통과."
    assert LocalFilter.from_db(_Conn(rows, [{"value": 500}])).evaluate(
        "로봇 발표", short
    ).status == "LOW_PRIORITY"
    kr.clear_cache()
    assert LocalFilter.from_db(_Conn(rows, [{"value": 20}])).evaluate(
        "로봇 발표", short
    ).status == "PASS"
