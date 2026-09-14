"""분석 배치의 처리량 상한·쿼터일 계산 테스트 (2026-09-08).

건수 캡은 폭주 방지용이고 처리량은 시간 예산이 정한다 — 이 원칙이 실제로
지켜지는지 검증한다. 09-08 09:16 배치(예산 720초)가 캡 280에 걸려 279건에서
끝난 실측 회귀가 여기 들어 있다.

운영 값은 코드 기본값이 아니라 app_settings에서 온다(우선순위: 환경변수 >
app_settings > 기본값). 기본값만 검사하면 운영에서 실제로 도는 조합을 하나도
못 보므로, 2026-09-08 운영 DB 값(PROD_APP_SETTINGS)으로도 같이 검사한다.
"""

import os
from datetime import date

import pytest

from kiro_batch.analyze import (
    _Shared,
    _process_stream,
    maybe_enable_last_call_drain,
    resolved_models,
    runaway_cap_floor,
)
from kiro_batch.config import Settings
from kiro_batch.quota import nightly_soft_limit

# 2026-09-08 운영 app_settings 실측값(직접 SELECT). 소프트리밋·브리프 예약은
# 코드 기본값과 같게 맞춰 뒀지만 건수 캡은 아직 280이라, 코드 기본값 400을
# 올려도 운영에서 캡을 밀어 올리는 건 analyze.runaway_cap_floor 쪽이다.
# 기사 분석이 멈추는 지점 — 코드 기본값 기준 (소프트리밋 - 브리프 예약).
_ARTICLE_STOP = Settings().article_daily_soft_limit - Settings().brief_daily_reserve

PROD_APP_SETTINGS = {
    "article_batch_max_count": 280,
    "article_batch_max_seconds": 420,
    "article_daily_soft_limit": 490,
    "article_models": "gemini-flash-lite-latest,gemini-3.1-flash-lite",
    "brief_daily_reserve": 10,
}


@pytest.fixture(autouse=True)
def _clear_batch_env(monkeypatch):
    """수동 실행 override가 테스트에 새어 들어오지 않게 한다.

    app_settings 반영은 같은 이름의 환경변수가 있으면 통째로 건너뛰므로
    (config.apply_app_settings), 그 키들도 함께 지운다.
    """
    for key in (
        "ARTICLE_MODEL",
        "ARTICLE_MODELS",
        "ARTICLE_BATCH_MAX_COUNT",
        "ARTICLE_BATCH_MAX_SECONDS",
        "ARTICLE_DAILY_SOFT_LIMIT",
        "BRIEF_DAILY_RESERVE",
        "ARTICLE_DRAIN_MAX_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)


def _prod_settings() -> Settings:
    s = Settings()
    s.apply_app_settings(PROD_APP_SETTINGS)
    return s


# ------------------------------------------------------------
# 건수 캡 하한
# ------------------------------------------------------------


def test_cap_floor_covers_measured_regression():
    """09-08 09:16 실측: 예산 720초 · 2모델 12 RPM에서 캡 280에 걸렸다.

    분당 24건이므로 720초의 이론 최대치는 288건 — 캡 280은 그보다 낮다.
    (그 실행은 duration 758초로 예산을 이미 넘긴 뒤였고 279건을 처리했다.
    즉 캡을 풀어 더 얻을 수 있던 건 많아야 9건이다. 이 하한의 값어치는
    처리량이 아니라 '무엇이 배치를 끝냈는지 읽을 수 있는 것'에 있다.)
    """
    assert runaway_cap_floor(720, 2, 12.0, 2) == 288 + 4
    assert runaway_cap_floor(720, 2, 12.0, 2) > 280


def test_cap_floor_scales_with_budget():
    for budget in (240, 360, 420, 540, 720, 1200):
        floor = runaway_cap_floor(budget, 2, 12.0, 2)
        # 속도 상한(분당 24건)으로 예산을 다 써도 캡에 닿지 않아야 한다
        assert floor > budget * 24 / 60


def test_cap_floor_single_model_is_half():
    """모델을 하나만 쓰면 속도 상한도 절반이므로 필요한 캡도 절반이다."""
    both = runaway_cap_floor(600, 2, 12.0, 2)
    single = runaway_cap_floor(600, 1, 12.0, 2)
    assert single < both
    assert single == 120 + 2


def test_cap_floor_handles_zero_and_negative_inputs():
    assert runaway_cap_floor(0, 2, 12.0, 2) == 4
    assert runaway_cap_floor(-10, 2, 12.0, 2) == 4
    assert runaway_cap_floor(600, 2, 0.0, 0) == 2  # 워커 0은 1로 본다


def test_default_cap_beats_every_current_slot_budget():
    """코드 기본 캡(400)이 지금 쓰는 어느 슬롯 예산에도 먼저 걸리지 않는다."""
    s = Settings()
    for budget in (240, 360, 420, 540, 720):
        assert s.article_batch_max_count >= runaway_cap_floor(
            budget, 2, s.article_target_rpm, s.article_stream_workers
        )


# ------------------------------------------------------------
# 운영 app_settings가 이기는 조합 (기본값만 보면 놓치는 것들)
# ------------------------------------------------------------


def test_app_settings_beats_the_code_default():
    """app_settings가 코드 기본값을 이긴다 — 캡 400은 운영에서 아직 280이다."""
    s = _prod_settings()
    assert s.article_batch_max_count == PROD_APP_SETTINGS["article_batch_max_count"]
    assert s.article_daily_soft_limit == PROD_APP_SETTINGS["article_daily_soft_limit"]
    assert s.article_batch_max_seconds == PROD_APP_SETTINGS["article_batch_max_seconds"]
    # 캡만 코드 기본값과 갈린다 (소프트리밋·예약은 2026-09-08에 맞춰 뒀다).
    assert Settings().article_batch_max_count != s.article_batch_max_count


def test_app_settings_cap_still_loses_to_the_long_slot_budget():
    """운영 캡 280은 07:05 슬롯(720초)에서 시간 예산보다 먼저 걸린다.

    그래서 처리량을 실제로 지키는 건 코드 기본값이 아니라 main()이
    app_settings 반영 뒤에 다시 계산하는 runaway_cap_floor다.
    """
    s = _prod_settings()
    floor = runaway_cap_floor(
        720, 2, s.article_target_rpm, s.article_stream_workers
    )
    assert floor == 292
    assert s.article_batch_max_count < floor


def test_app_settings_night_limit_still_clears_the_night_slots():
    """운영 소프트리밋에서 야간 슬롯(22:50·05:10)이 막히지 않는다.

    실측 건당 2.7초·1.05콜/건, 2모델 병렬 → 예산 합계 600초면 모델당 약 117콜.
    """
    s = _prod_settings()
    night_cap = (
        nightly_soft_limit(
            s.article_daily_soft_limit, s.morning_reserve_calls, kst_hour=22
        )
        - s.brief_daily_reserve
    )
    expected = (
        s.article_daily_soft_limit - s.morning_reserve_calls - s.brief_daily_reserve
    )
    assert night_cap == expected  # 490 - 150 - 10 = 330
    assert (600 / 2.7) * 1.05 / 2 < night_cap


# ------------------------------------------------------------
# 모델 해석
# ------------------------------------------------------------


def test_resolved_models_defaults_to_two_streams():
    assert resolved_models(Settings()) == [
        "gemini-flash-lite-latest",
        "gemini-3.1-flash-lite",
    ]


def test_resolved_models_honors_manual_single_model(monkeypatch):
    monkeypatch.setenv("ARTICLE_MODEL", "gemini-3.1-flash-lite")
    s = Settings()
    s.article_model = "gemini-3.1-flash-lite"
    assert resolved_models(s) == ["gemini-3.1-flash-lite"]


def test_resolved_models_falls_back_when_list_empty():
    s = Settings()
    s.article_models = " , "
    assert resolved_models(s) == [s.article_model]


# ------------------------------------------------------------
# 수동 실행 건수와 막차 소진의 상호작용 (결함 D)
# ------------------------------------------------------------


@pytest.fixture
def near_quota_reset(monkeypatch):
    """리셋 1시간 전 — 실행 시각에 따라 결과가 달라지지 않게 고정한다."""
    monkeypatch.setattr(
        "kiro_batch.analyze.hours_until_quota_reset", lambda: 1.0
    )


def _slot_capacity(budget_seconds: int = 420) -> int:
    """main()이 막차 판정에 넘기는 잣대 — 이 예산으로 처리 가능한 건수."""
    s = Settings()
    return runaway_cap_floor(
        budget_seconds, 2, s.article_target_rpm, s.article_stream_workers
    )


def test_drain_allowed_when_max_count_input_is_empty(monkeypatch, near_quota_reset):
    """워크플로 기본값이 빈 값이 됐으므로 수동 실행도 막차 소진에 참여한다."""
    monkeypatch.setenv("ARTICLE_BATCH_MAX_COUNT", "")
    s = Settings()
    s.article_batch_max_seconds = 420
    assert maybe_enable_last_call_drain(s, pending=5000) is True
    assert s.article_batch_max_count == 999
    assert s.article_batch_max_seconds == 540


def test_manual_count_no_longer_shrinks_the_time_budget(monkeypatch, near_quota_reset):
    """운영자가 적은 건수는 '상한'일 뿐 시간 예산을 깎지 않는다 (결함 D).

    예전에는 건수를 적으면 막차 소진이 통째로 꺼져 KST 14~17시 예산이
    540 → 420초로 줄었다. 상한을 정했더니 더 적게 처리되는 셈이었다.
    """
    monkeypatch.setenv("ARTICLE_BATCH_MAX_COUNT", "300")
    s = Settings()
    s.article_batch_max_count = 300
    s.article_batch_max_seconds = 420  # KST 14~17시 기본 예산
    assert maybe_enable_last_call_drain(
        s, pending=5000, batch_capacity=_slot_capacity()
    ) is True
    assert s.article_batch_max_count == 300  # 지정한 상한은 999로 덮지 않는다
    assert s.article_batch_max_seconds == 540


def test_drain_decision_ignores_the_operator_number(monkeypatch, near_quota_reset):
    """건수를 크게 적었다고 막차 소진이 꺼지면 안 된다.

    판정 잣대는 '이번 배치가 시간 예산으로 처리할 수 있는 양'이지
    운영자가 고른 숫자가 아니다.
    """
    monkeypatch.setenv("ARTICLE_BATCH_MAX_COUNT", "5000")
    s = Settings()
    s.article_batch_max_count = 5000
    s.article_batch_max_seconds = 420
    capacity = _slot_capacity()
    assert capacity == 172
    # 대기 1000건은 지정값(5000)보다 적지만 처리 가능량(172)보다는 많다
    assert maybe_enable_last_call_drain(
        s, pending=1000, batch_capacity=capacity
    ) is True
    assert s.article_batch_max_count == 5000
    assert s.article_batch_max_seconds == 540


def test_no_drain_when_backlog_is_small(near_quota_reset):
    """태울 백로그가 없으면 예산도 그대로 — 필요 없는 Actions 분을 안 쓴다."""
    s = Settings()
    s.article_batch_max_seconds = 420
    assert maybe_enable_last_call_drain(
        s, pending=10, batch_capacity=_slot_capacity()
    ) is False
    assert s.article_batch_max_seconds == 420
    assert s.article_batch_max_count == Settings().article_batch_max_count


# ------------------------------------------------------------
# 쿼터일 경계 (결함 I) — 배치가 리셋(KST 17시)을 걸칠 때
# ------------------------------------------------------------

DAY1 = date(2026, 9, 7)
DAY2 = date(2026, 9, 8)
MODEL = "gemini-flash-lite-latest"


class _FakeUsage:
    """UsageRecorder 대역 — DB 없이 카운터만 센다."""

    def __init__(self) -> None:
        self.run_id = "test"
        self.counts = {"collected": 0, "analyzed": 0, "api_calls": 0}
        self.stage_seconds: dict[str, int] = {}

    def elapsed_seconds(self) -> float:
        return 0.0  # 시간 예산으로는 끝나지 않게 고정


class _FakeResult:
    data = {"ok": True}
    model_name = MODEL
    raw_text = "{}"
    input_tokens = 10
    output_tokens = 20


def _drive_stream(
    monkeypatch,
    *,
    seeded_calls: int,
    job_count: int,
    reset_after_call: int | None,
) -> tuple[list[tuple[str, date]], _Shared]:
    """_process_stream을 DB·Gemini 없이 돌린다.

    reset_after_call: 이 번째 호출이 끝날 때 쿼터 리셋이 일어난 것으로 본다
        (호출 하나가 5~7초 걸리므로 실제로 걸칠 수 있다). None이면 리셋 없음.
    """
    recorded: list[tuple[str, date]] = []
    state = {"calls": 0, "reset": False}

    def fake_quota_date():
        return DAY2 if state["reset"] else DAY1

    def fake_calls_today(conn, model, quota_date):
        base = seeded_calls if quota_date == DAY1 else 0
        return base + sum(1 for m, d in recorded if m == model and d == quota_date)

    def fake_record(conn, model, quota_date, status, error_code=None, job_id=None):
        recorded.append((model, quota_date))

    jobs = [
        {"id": f"job-{i}", "cluster_id": f"cluster-{i}", "attempt_count": 0}
        for i in range(job_count)
    ]

    def fake_acquire(conn, worker_id, prefer_fresh=True):
        return jobs.pop(0) if jobs else None

    class _FakeProvider:
        def analyze_article(self, prompt, model):
            state["calls"] += 1
            if reset_after_call is not None and state["calls"] >= reset_after_call:
                state["reset"] = True
            return _FakeResult()

    class _FakeLimiter:
        def acquire(self) -> None:
            return None

    patches = {
        "quota_date_pt": fake_quota_date,
        "todays_call_count": fake_calls_today,
        "record_gemini_call": fake_record,
        "acquire_next_job": fake_acquire,
        "build_article_prompt": lambda conn, cluster_id, settings: "prompt",
        "validate_analysis": lambda data: ({"summary": "x"}, "PASS"),
        "save_analysis": lambda *a, **k: "analysis-id",
        "publish_analysis": lambda *a, **k: None,
        "complete_job": lambda *a, **k: None,
        # 야간 예약분은 다른 테스트가 본다 — 여기서는 실행 시각과 무관하게 주간
        "nightly_soft_limit": lambda soft_limit, reserve, kst_hour: soft_limit,
    }
    for name, fake in patches.items():
        monkeypatch.setattr(f"kiro_batch.analyze.{name}", fake)

    settings = Settings()
    shared = _Shared()
    _process_stream(
        object(), _FakeProvider(), MODEL, settings, _FakeUsage(), shared, _FakeLimiter()
    )
    return recorded, shared


def test_calls_after_the_reset_count_toward_the_new_quota_day(monkeypatch):
    """리셋을 걸친 배치의 호출은 새 쿼터일에 쌓인다 (결함 I).

    쿼터일을 배치 시작 때 한 번만 잡으면, 리셋 뒤 호출이 구글 집계로는 새
    쿼터일인데 우리 행에는 어제 날짜로 남는다 — 새 쿼터일의 우리 카운터가
    구글보다 **작아져서** 소프트리밋이 늦게 걸린다. 막차 소진(예산 540초)이
    리셋 직전 창에서 도는 만큼 실제로 걸치는 경로다.

    어제 몫이 한도 직전이므로, 고치기 전에는 첫 호출을 어제로 기록하고
    두 번째 검사에서 '일일 내부 목표 도달'로 배치가 멈췄다.
    """
    recorded, shared = _drive_stream(
        monkeypatch, seeded_calls=_ARTICLE_STOP - 1, job_count=3, reset_after_call=1
    )
    assert recorded == [(MODEL, DAY2)] * 3
    assert shared.processed == 3


def test_daily_limit_still_stops_the_batch_without_a_reset(monkeypatch):
    """리셋이 없으면 그날 한도(소프트리밋 - 브리프 예약)에서 그대로 멈춘다.

    결함 I 수정이 쿼터 안전장치를 무르게 하지 않았는지 보는 짝 테스트다.
    """
    recorded, shared = _drive_stream(
        monkeypatch, seeded_calls=_ARTICLE_STOP - 1, job_count=3, reset_after_call=None
    )
    assert recorded == [(MODEL, DAY1)]
    assert shared.processed == 1


# ------------------------------------------------------------
# 하루 한도·예약분 (설정 변경이 안전장치와 충돌하지 않는지)
# ------------------------------------------------------------


def test_soft_limit_leaves_headroom_under_hard_limit():
    """소프트리밋 + 브리프 예약이 모델당 하드 한도 500을 넘지 않는다."""
    s = Settings()
    assert s.article_daily_soft_limit < 500
    # 기사 분석이 실제로 멈추는 지점
    assert s.article_daily_soft_limit - s.brief_daily_reserve <= 480


def test_night_limit_still_leaves_room_for_current_night_slots():
    """야간 예약분이 지금의 야간 슬롯(22:50·05:10 = 600초)을 막지 않는다.

    실측 건당 2.7초·1.05콜/건, 2모델 병렬 → 모델당 약 117콜.
    """
    s = Settings()
    night_cap = nightly_soft_limit(
        s.article_daily_soft_limit, s.morning_reserve_calls, kst_hour=22
    ) - s.brief_daily_reserve
    night_calls_per_model = (600 / 2.7) * 1.05 / 2
    assert night_calls_per_model < night_cap


def test_night_and_day_limits_differ_by_the_morning_reserve():
    s = Settings()
    day = nightly_soft_limit(
        s.article_daily_soft_limit, s.morning_reserve_calls, kst_hour=9
    )
    night = nightly_soft_limit(
        s.article_daily_soft_limit, s.morning_reserve_calls, kst_hour=2
    )
    assert day == s.article_daily_soft_limit
    assert day - night == s.morning_reserve_calls


def test_env_override_still_wins_for_batch_count(monkeypatch):
    monkeypatch.setenv("ARTICLE_BATCH_MAX_COUNT", "42")
    assert Settings.from_env().article_batch_max_count == 42


def test_empty_env_override_keeps_default(monkeypatch):
    """워크플로가 빈 값을 넘겨도 기본값이 유지돼야 한다."""
    monkeypatch.setenv("ARTICLE_BATCH_MAX_COUNT", "")
    assert Settings.from_env().article_batch_max_count == Settings().article_batch_max_count
    assert os.environ["ARTICLE_BATCH_MAX_COUNT"] == ""
