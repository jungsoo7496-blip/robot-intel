"""Gemini 일일 쿼터 날짜 테스트 (설계 §7.1, tasks §8.4).

경계는 UTC-8 자정 = 연중 KST 17시로 고정이다. 종전에는 IANA 타임존을 써서
여름에 KST 16시로 옮겨간다고 보았으나, 실측에서 Google 콘솔 소진량과
어긋나 서머타임을 따르지 않음을 확인했다 (2026-08-12).
"""

from datetime import date, datetime, timezone

from kiro_batch.quota import hours_until_quota_reset, quota_date_pt


def test_winter_boundary_is_utc_minus_8():
    # 08:00 UTC = 00:00 (UTC-8) = KST 17:00
    assert quota_date_pt(datetime(2026, 1, 15, 7, 59, tzinfo=timezone.utc)) == date(2026, 1, 14)
    assert quota_date_pt(datetime(2026, 1, 15, 8, 0, tzinfo=timezone.utc)) == date(2026, 1, 15)


def test_summer_boundary_does_not_shift_for_dst():
    # 서머타임에도 경계는 그대로 08:00 UTC다. IANA를 쓰면 07:00 UTC로
    # 한 시간 당겨져, KST 16~17시에 "오늘 0콜"이라 오판하고 429를 부른다.
    assert quota_date_pt(datetime(2026, 7, 15, 6, 59, tzinfo=timezone.utc)) == date(2026, 7, 14)
    assert quota_date_pt(datetime(2026, 7, 15, 7, 30, tzinfo=timezone.utc)) == date(2026, 7, 14)
    assert quota_date_pt(datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)) == date(2026, 7, 15)


def test_kst_1600_to_1700_still_belongs_to_previous_quota_day():
    """실측 회귀 (2026-08-12): KST 16:19 배치를 새 쿼터일로 세어
    230콜로 봤는데 콘솔은 옛 쿼터일에 얹어 424/500을 표시했다."""
    kst_1619 = datetime(2026, 8, 12, 7, 19, tzinfo=timezone.utc)  # KST 16:19
    kst_1701 = datetime(2026, 8, 12, 8, 1, tzinfo=timezone.utc)   # KST 17:01
    assert quota_date_pt(kst_1619) == date(2026, 8, 11)
    assert quota_date_pt(kst_1701) == date(2026, 8, 12)


def test_kst_midnight_does_not_reset():
    before = quota_date_pt(datetime(2026, 8, 4, 14, 59, tzinfo=timezone.utc))
    after = quota_date_pt(datetime(2026, 8, 4, 15, 1, tzinfo=timezone.utc))
    assert before == after


def test_hours_until_reset_matches_kst_1700():
    # KST 15:00 (06:00 UTC) → 리셋(KST 17:00)까지 2시간
    assert hours_until_quota_reset(
        datetime(2026, 8, 12, 6, 0, tzinfo=timezone.utc)
    ) == 2.0
    # 서머타임이 아닌 1월에도 같다
    assert hours_until_quota_reset(
        datetime(2026, 1, 12, 6, 0, tzinfo=timezone.utc)
    ) == 2.0


def test_naive_datetime_treated_as_utc():
    naive = datetime(2026, 1, 15, 8, 0)
    assert quota_date_pt(naive) == date(2026, 1, 15)
