"""속도 제한 계산 — 분당 한도 안에서 최대한 촘촘하게 (2026-08-11).

고정 sleep은 생성이 오래 걸린 뒤에도 무조건 더 쉬어서 한도를 남긴 채
Actions 시간을 태운다. next_slot은 경과분을 빼고 필요한 만큼만 재운다.
"""

from kiro_batch.freshness import batch_budget_seconds, next_slot
from kiro_batch.ratelimit import RateLimiter


class TestNextSlot:
    def test_no_wait_when_interval_already_elapsed(self):
        # 직전 호출이 10초 전, 최소 간격 5초 → 지금 바로
        assert next_slot(last_slot=0.0, now=10.0, min_interval=5.0) == 10.0

    def test_waits_remaining_time(self):
        # 직전 호출이 2초 전, 최소 간격 5초 → 3초 더 (=5.0 시점)
        assert next_slot(last_slot=0.0, now=2.0, min_interval=5.0) == 5.0

    def test_slots_are_monotonic_under_burst(self):
        # 워커 여러 개가 동시에 몰려도 슬롯이 간격만큼 벌어진다.
        # 시작 상태는 RateLimiter 초기화와 같다 (직전 슬롯 = now - interval)
        s1 = next_slot(-5.0, 0.0, 5.0)
        s2 = next_slot(s1, 0.0, 5.0)
        s3 = next_slot(s2, 0.0, 5.0)
        assert (s1, s2, s3) == (0.0, 5.0, 10.0)

    def test_generation_slower_than_interval_means_no_wait(self):
        # 생성이 8초 걸렸다면(now=8) 간격 5초는 이미 충족 — 대기 0
        assert next_slot(last_slot=0.0, now=8.0, min_interval=5.0) == 8.0


class TestRateLimiter:
    def test_interval_from_rpm(self):
        assert RateLimiter(12.0).min_interval == 5.0
        assert RateLimiter(15.0).min_interval == 4.0

    def test_first_call_is_immediate(self):
        assert RateLimiter(12.0).acquire() == 0.0

    def test_rejects_nonpositive_rpm(self):
        import pytest

        with pytest.raises(ValueError):
            RateLimiter(0)


class TestBatchBudget:
    def test_morning_gets_the_most(self):
        # 조간 준비 시간대 — 밤사이 쌓인 것을 8시 전에 턴다
        assert batch_budget_seconds(5, 420) == 540
        assert batch_budget_seconds(7, 420) == 540
        # 조간이 다른 어느 시간대보다 크다 (배분의 핵심 성질)
        assert batch_budget_seconds(5, 420) > batch_budget_seconds(10, 420)
        assert batch_budget_seconds(5, 420) > batch_budget_seconds(22, 420)

    def test_reading_window_gets_a_refresh(self):
        # 08~11:30 열람 중에도 한 번 갱신된다 (사용자 요청 2026-08-11)
        assert batch_budget_seconds(9, 420) == 360
        assert batch_budget_seconds(10, 420) == 360

    def test_delayed_morning_batch_keeps_its_budget(self):
        # 크론은 +38~137분 밀려서 뜬다. 10:10 슬롯이 12:27에 떠도 열람
        # 시간대 예산(360초)을 받아야 한다 — 실제로 08-12에 180초만 받아
        # 51건에서 끊겼다. 지연이 예산까지 깎으면 이중 손해다.
        assert batch_budget_seconds(12, 420) == 360
        assert batch_budget_seconds(13, 420) == 360

    def test_quota_drain_window_keeps_base(self):
        assert batch_budget_seconds(15, 420) == 420
        # 막차(15:05)가 16시대로 밀려도 막차 예산을 유지한다
        assert batch_budget_seconds(16, 420) == 420

    def test_night_is_moderate(self):
        assert batch_budget_seconds(22, 420) == 240
        assert batch_budget_seconds(2, 420) == 240

    def test_daily_total_stays_within_current_spend(self):
        # 실제 스케줄(05·07·10·15·22시) 합계 ≤ 기존 420×5
        total = (
            batch_budget_seconds(5, 420)
            + batch_budget_seconds(7, 420)
            + batch_budget_seconds(10, 420)
            + batch_budget_seconds(15, 420)
            + batch_budget_seconds(22, 420)
        )
        assert total <= 420 * 5
