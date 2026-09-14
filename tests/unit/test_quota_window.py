"""오전 예약 쿼터 (사용자 지시 2026-08-08).

쿼터의 하루는 KST 17시(UTC-8 자정) 시작이지만 독자의 하루는 아침이다 —
야간에 전량 소진하면 조간 뉴스가 밀리므로 야간 상한에서 오전 몫을 뺀다.
경계는 리셋 시각과 같아야 한다 (2026-08-12: 16 → 17 정정).
"""

from kiro_batch.quota import nightly_soft_limit


def test_night_hours_reserve_morning_quota():
    # 야간(17시~익일 6시): 450 - 150 = 300 상한
    for hour in (17, 20, 23, 0, 3, 5):
        assert nightly_soft_limit(450, 150, hour) == 300


def test_day_hours_full_limit():
    # 주간(6시~17시): 원래 상한 그대로 — 조간 분석이 예약분을 쓴다
    for hour in (6, 8, 11, 15, 16):
        assert nightly_soft_limit(450, 150, hour) == 450


def test_boundary_matches_quota_reset_hour():
    # 리셋 직전 한 시간(KST 16시대)은 아직 옛 쿼터일이므로 주간 상한을 쓰면
    # 안 되는 게 아니라, '남은 것을 태우는' 막차 구간이라 상한을 안 깎는다.
    assert nightly_soft_limit(450, 150, 16) == 450  # 16:59까지 주간
    assert nightly_soft_limit(450, 150, 17) == 300  # 17:00 리셋부터 야간
    assert nightly_soft_limit(450, 150, 5) == 300   # 05:59까지 야간
    assert nightly_soft_limit(450, 150, 6) == 450   # 06:00부터 주간


def test_reserve_never_negative():
    assert nightly_soft_limit(100, 150, 20) == 0
