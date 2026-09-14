"""조간 신선도 순수 함수 — 호 경계는 오프바이원이 나기 쉬워 경계값을 고정한다."""

from datetime import datetime, timedelta, timezone

from kiro_batch.freshness import (
    FRESH_WINDOW_HOURS,
    clamp_source_time,
    current_edition_at,
    edition_of,
    freshness_tier,
    is_fresh,
    is_live_edition,
    should_drain,
    starvation_slots,
)

KST = timezone(timedelta(hours=9))


def kst(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=KST)


class TestEditionBoundary:
    def test_before_8am_belongs_to_today_edition(self):
        # 사용자가 지목한 실제 사례: 원문 8/10 06:00 기사
        assert edition_of(kst(2026, 8, 10, 6, 0)) == kst(2026, 8, 10).date()

    def test_exactly_8am_starts_next_edition(self):
        assert edition_of(kst(2026, 8, 10, 8, 0)) == kst(2026, 8, 11).date()

    def test_one_minute_before_8am(self):
        assert edition_of(kst(2026, 8, 10, 7, 59)) == kst(2026, 8, 10).date()

    def test_evening_belongs_to_next_morning(self):
        assert edition_of(kst(2026, 8, 10, 22, 0)) == kst(2026, 8, 11).date()

    def test_current_edition_is_one_day_behind_edition_of(self):
        t = kst(2026, 8, 10, 8, 4)
        assert current_edition_at(t) == kst(2026, 8, 10).date()
        # 같은 시각에 발행된 기사는 '다음 호'라서 아직 살아 있다
        assert edition_of(t) == kst(2026, 8, 11).date()

    def test_live_edition_detection(self):
        now = kst(2026, 8, 10, 9, 0)  # 8/10호가 열려 있는 시점
        # 8/10 08:30 발행 → 8/11호 → 아직 안 나온 호라 살아 있음
        assert is_live_edition(kst(2026, 8, 10, 8, 30), now) is True
        # 8/10 06:00 발행 → 8/10호 → 이미 열린 호에 소급 삽입 = 사실상 못 봄
        assert is_live_edition(kst(2026, 8, 10, 6, 0), now) is False


class TestClampSourceTime:
    def test_future_publish_clamped_to_now(self):
        now = kst(2026, 8, 11, 10, 0)
        future = now + timedelta(hours=6)
        assert clamp_source_time(future, now) == now

    def test_past_publish_untouched(self):
        now = kst(2026, 8, 11, 10, 0)
        past = now - timedelta(hours=3)
        assert clamp_source_time(past, now) == past

    def test_none_stays_none(self):
        assert clamp_source_time(None, kst(2026, 8, 11)) is None


class TestFreshnessTier:
    def test_low_priority_shares_tier_with_general(self):
        # p45(저확신)가 p40 뒤에 줄 서면 가장 신선한 재료가 굶는다
        assert freshness_tier(40) == freshness_tier(45) == 40

    def test_other_tiers_stay_separate(self):
        assert freshness_tier(10) == 10
        assert freshness_tier(15) == 10
        assert freshness_tier(30) == freshness_tier(35) == 30
        assert freshness_tier(60) == 60


class TestIsFresh:
    def test_recent_is_fresh(self):
        now = kst(2026, 8, 11, 8, 0)
        assert is_fresh(now - timedelta(hours=5), now) is True

    def test_beyond_window_is_not_fresh(self):
        now = kst(2026, 8, 11, 8, 0)
        assert is_fresh(now - timedelta(hours=FRESH_WINDOW_HOURS + 1), now) is False

    def test_unknown_time_is_not_fresh(self):
        assert is_fresh(None, kst(2026, 8, 11, 8, 0)) is False

    def test_future_is_not_fresh(self):
        now = kst(2026, 8, 11, 8, 0)
        assert is_fresh(now + timedelta(hours=2), now) is False


class TestStarvationSlots:
    def test_reserves_a_fifth(self):
        assert starvation_slots(60) == 12
        assert starvation_slots(40) == 8

    def test_always_at_least_one(self):
        assert starvation_slots(3) == 1
        assert starvation_slots(1) == 1

    def test_zero_capacity(self):
        assert starvation_slots(0) == 0


class TestShouldDrain:
    def test_drains_near_reset_with_backlog(self):
        assert should_drain(2.5, 100, 60) is True

    def test_no_drain_when_reset_far(self):
        assert should_drain(6.0, 100, 60) is False

    def test_no_drain_without_backlog(self):
        assert should_drain(2.5, 10, 60) is False

    def test_no_drain_at_or_past_reset(self):
        # 리셋이 지난 직후(0 이하)에는 새 쿼터일이므로 막차가 아니다
        assert should_drain(0.0, 100, 60) is False
