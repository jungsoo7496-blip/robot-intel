"""주간 브리프 기간 계산 테스트 (외부 리뷰 지시 10항).

KST 고정 주차: 월요일 00:00 ~ 일요일 23:59:59, 다음 월요일 발행.
"""

from datetime import date, datetime

import pytest

from kiro_batch.generate_brief import (
    WeekStartError,
    kst_bounds,
    previous_completed_week,
    resolve_week_start,
)


def test_monday_returns_last_full_week():
    # 월요일: 직전 월~일이 완결 주차
    start, end = previous_completed_week(date(2026, 8, 10))  # 월
    assert start == date(2026, 8, 3)
    assert end == date(2026, 8, 9)
    assert start.weekday() == 0 and end.weekday() == 6


def test_midweek_returns_previous_week():
    # 수요일: 이번 주는 미완결이므로 지난주를 반환
    start, end = previous_completed_week(date(2026, 8, 12))  # 수
    assert (start, end) == (date(2026, 8, 3), date(2026, 8, 9))


def test_sunday_still_previous_week():
    # 일요일: 당일 23:59까지 이번 주가 진행 중 → 지난주
    start, end = previous_completed_week(date(2026, 8, 16))  # 일
    assert (start, end) == (date(2026, 8, 3), date(2026, 8, 9))


def test_month_boundary():
    # 9/2(수) → 8/24(월)~8/30(일): 월 경계를 넘는 주차
    start, end = previous_completed_week(date(2026, 9, 2))
    assert (start, end) == (date(2026, 8, 24), date(2026, 8, 30))


def test_year_boundary():
    # 2027-01-04(월) → 2026-12-28(월)~2027-01-03(일): 연도 경계
    start, end = previous_completed_week(date(2027, 1, 4))
    assert (start, end) == (date(2026, 12, 28), date(2027, 1, 3))


def test_kst_bounds_cover_full_week():
    start_ts, end_ts = kst_bounds(date(2026, 8, 3), date(2026, 8, 9))
    # 시작: 월요일 00:00 KST
    assert start_ts.isoformat() == "2026-08-03T00:00:00+09:00"
    # 끝: 다음 월요일 00:00 KST (미만 비교 → 일요일 23:59:59.999 포함)
    assert end_ts.isoformat() == "2026-08-10T00:00:00+09:00"
    # 일요일 밤 항목이 포함되는지
    sunday_night = datetime.fromisoformat("2026-08-09T23:59:59+09:00")
    assert start_ts <= sunday_night < end_ts


def test_candidate_caps_defined():
    from kiro_batch.generate_brief import CANDIDATE_CAPS, CANDIDATE_TOTAL_MAX

    assert CANDIDATE_CAPS == {"정책": 10, "산업": 12, "기술": 12}
    assert 20 <= CANDIDATE_TOTAL_MAX <= 40


# ------------------------------------------------------------
# resolve_week_start — BRIEF_WEEK_START(특정 주차 생성·재생성) 해석 규칙
# ------------------------------------------------------------

TODAY = date(2026, 9, 8)  # 화요일


def test_week_start_monday_is_kept_as_is():
    start, end, note = resolve_week_start("2026-08-31", TODAY)  # 월
    assert (start, end) == (date(2026, 8, 31), date(2026, 9, 6))
    assert start.weekday() == 0 and end.weekday() == 6
    assert note is None


def test_week_start_midweek_is_adjusted_to_monday_with_note():
    start, end, note = resolve_week_start("2026-08-05", TODAY)  # 수
    assert (start, end) == (date(2026, 8, 3), date(2026, 8, 9))
    assert note is not None
    assert "2026-08-05(수)" in note and "2026-08-03" in note


def test_week_start_sunday_belongs_to_preceding_monday():
    # 일요일은 그 주의 마지막 날 — 다음 주가 아니라 같은 주로 본다
    start, end, _ = resolve_week_start("2026-08-09", TODAY)  # 일
    assert (start, end) == (date(2026, 8, 3), date(2026, 8, 9))


def test_week_start_surrounding_whitespace_is_ignored():
    start, _, note = resolve_week_start("  2026-08-31 \n", TODAY)
    assert start == date(2026, 8, 31) and note is None


def test_week_start_current_incomplete_week_rejected():
    # 이번 주 월요일(9/7): 오늘이 화요일이라 아직 진행 중
    with pytest.raises(WeekStartError):
        resolve_week_start("2026-09-07", TODAY)


def test_week_start_future_week_rejected():
    with pytest.raises(WeekStartError):
        resolve_week_start("2026-09-14", TODAY)


def test_week_start_allowed_from_next_monday_only():
    # 8/31~9/6 주차: 9/6(일)에는 아직 거부, 9/7(월)부터 허용 — 정기 배치와 같은 기준
    with pytest.raises(WeekStartError):
        resolve_week_start("2026-08-31", date(2026, 9, 6))
    start, end, _ = resolve_week_start("2026-08-31", date(2026, 9, 7))
    assert (start, end) == (date(2026, 8, 31), date(2026, 9, 6))


def test_week_start_rejection_message_is_readable():
    with pytest.raises(WeekStartError) as exc:
        resolve_week_start("2026-09-09", TODAY)
    msg = str(exc.value)
    assert "2026-09-07~2026-09-13" in msg and "월요일" in msg


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "2026/08/31", "20260831", "2026-8-3", "2026-02-30", "다음주"],
)
def test_week_start_bad_format_rejected(raw):
    with pytest.raises(WeekStartError):
        resolve_week_start(raw, TODAY)


def test_week_start_consistent_with_previous_completed_week():
    start, end = previous_completed_week(TODAY)
    assert resolve_week_start(str(start), TODAY)[:2] == (start, end)


def test_week_start_error_is_value_error():
    # 호출 측이 ValueError로 뭉뚱그려 잡아도 동작하도록
    assert issubclass(WeekStartError, ValueError)


# ------------------------------------------------------------
# 실패 사유 — 상한에 걸려도 진짜 원인을 남긴다 (2026-09-08 운영자 피드백 7)
# ------------------------------------------------------------

from kiro_batch.config import Settings  # noqa: E402
from kiro_batch.gemini_provider import AIResult, ProviderServerError  # noqa: E402
from kiro_batch.generate_brief import (  # noqa: E402
    MAX_BRIEF_CALLS,
    REASON_RATE_LIMIT,
    REASON_RETRY_LIMIT,
    REASON_SERVER_BUSY,
    Section3,
    generate_section,
    merge_failure_reasons,
)


class _FakeCursor:
    """record_gemini_call이 쓰는 것만 흉내 낸다 — DB 없이 돌린다."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        pass


class _FakeConn:
    def cursor(self):
        return _FakeCursor()

    def commit(self):
        pass


class _ScriptedProvider:
    """호출 순서대로 미리 정한 결과(예외 또는 응답 dict)를 돌려주는 가짜 공급자."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.models_called: list[str] = []

    def analyze_article(self, prompt, model_name):  # 인터페이스 충족용
        raise NotImplementedError

    def generate_brief_section(self, prompt, model_name):
        self.models_called.append(model_name)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return AIResult(
            data=outcome,
            model_name=model_name,
            input_tokens=None,
            output_tokens=None,
            raw_text="",
        )


def _brief_settings() -> Settings:
    s = Settings()
    s.brief_model = "primary-model"
    s.brief_fallback_model = "fallback-model"
    return s


def test_every_section_can_reach_the_fallback_model():
    # 회귀: 상한이 5였을 때 섹션3만 예비 모델을 못 써서 08-17·08-24·09-01
    # 브리프가 '전주 대비 변화·KIRO 시사점'을 비운 채 발행됐다.
    assert MAX_BRIEF_CALLS >= 3 * 2

    provider = _ScriptedProvider(
        [
            ProviderServerError("503"), {"changes_from_previous": "1"},
            ProviderServerError("503"), {"changes_from_previous": "2"},
            ProviderServerError("503"), {"changes_from_previous": "3"},
        ]
    )
    conn, settings, calls_used = _FakeConn(), _brief_settings(), [0]
    for _ in range(3):
        section, reason = generate_section(
            provider, conn, settings, "prompt", Section3, calls_used
        )
        assert section is not None
        assert reason is None
    assert calls_used[0] == 6
    assert provider.models_called[-1] == "fallback-model"


def test_call_cap_keeps_the_real_failure_reason():
    # 예비 모델 시도 직전에 상한에 닿아도 화면에는 상한이 아니라 503이 남아야 한다
    provider = _ScriptedProvider([ProviderServerError("503 UNAVAILABLE")])
    calls_used = [MAX_BRIEF_CALLS - 1]
    section, reason = generate_section(
        provider, _FakeConn(), _brief_settings(), "prompt", Section3, calls_used
    )
    assert section is None
    assert reason == REASON_SERVER_BUSY


def test_call_cap_before_any_attempt_reports_retry_limit():
    provider = _ScriptedProvider([])
    calls_used = [MAX_BRIEF_CALLS]
    section, reason = generate_section(
        provider, _FakeConn(), _brief_settings(), "prompt", Section3, calls_used
    )
    assert section is None
    assert reason == REASON_RETRY_LIMIT
    assert provider.models_called == []


def test_retry_limit_wording_is_not_read_as_gemini_quota():
    # 운영자가 '예산 소진'을 'Gemini 하루 한도를 다 썼다'로 읽었다 (피드백 7)
    assert "예산" not in REASON_RETRY_LIMIT
    assert REASON_RETRY_LIMIT == "AI 재시도 횟수 초과"


def test_merge_failure_reasons_dedupes_and_drops_blanks():
    assert merge_failure_reasons(
        (REASON_SERVER_BUSY, REASON_SERVER_BUSY, None)
    ) == [REASON_SERVER_BUSY]
    assert merge_failure_reasons((None, "", "   ")) == []


def test_merge_failure_reasons_puts_the_real_cause_first():
    # '재시도 횟수 초과'는 결과이지 원인이 아니다 — 화면은 맨 앞만 보여 준다
    assert merge_failure_reasons((REASON_RETRY_LIMIT, REASON_SERVER_BUSY))[0] == (
        REASON_SERVER_BUSY
    )
    assert merge_failure_reasons((REASON_SERVER_BUSY, REASON_RATE_LIMIT))[0] == (
        REASON_RATE_LIMIT
    )


def test_merge_failure_reasons_keeps_unknown_text_last():
    assert merge_failure_reasons(("알 수 없는 오류", REASON_RATE_LIMIT)) == [
        REASON_RATE_LIMIT,
        "알 수 없는 오류",
    ]
