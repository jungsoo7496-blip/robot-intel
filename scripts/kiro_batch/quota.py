"""Gemini 일일 쿼터 날짜 계산 (설계 §7.1, tasks §8.4).

Gemini RPD 카운터는 KST 자정이 아니라 태평양 기준 자정에 리셋된다.

2026-08-12 정정 — 서머타임을 따르지 않는다:
종전에는 ZoneInfo("America/Los_Angeles")를 써서 여름에는 UTC-7(=KST 16시),
겨울에는 UTC-8(=KST 17시)로 경계가 옮겨간다고 보았다. 그런데 실측에서
Google 콘솔의 소진량과 우리 집계가 어긋났다 — KST 16:19~16:45에 돌린
배치를 우리는 '새 쿼터일'로 세어 230콜로 봤는데, 콘솔은 옛 쿼터일에 얹어
424/500으로 표시했다. 즉 Google은 서머타임과 무관하게 **UTC-8 고정**으로
끊는다(연중 KST 17시).

이 한 시간 차이는 그냥 오차가 아니라 사고를 만든다. KST 16~17시에 우리는
"오늘 쓴 게 0콜"이라고 믿고 소프트리밋 450까지 새로 태우는데, 실제로는
어제 몫이 거의 찬 상태라 곧바로 429가 쏟아진다. 그래서 IANA 타임존 대신
고정 오프셋을 쓴다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# Gemini 쿼터 경계 — UTC-8 고정 (서머타임 미적용, 연중 KST 17시)
_QUOTA_TZ = timezone(timedelta(hours=-8))


def hours_until_quota_reset(now_utc: datetime | None = None) -> float:
    """다음 쿼터 리셋(UTC-8 자정 = KST 17시)까지 남은 시간."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_q = now_utc.astimezone(_QUOTA_TZ)
    next_midnight = (now_q + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (next_midnight - now_q).total_seconds() / 3600


def quota_date_pt(now_utc: datetime | None = None) -> date:
    """현재(또는 주어진 UTC 시각)의 쿼터 날짜 (UTC-8 고정 기준)."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(_QUOTA_TZ).date()


# 오전 예약 쿼터 (사용자 지시 2026-08-08):
# 쿼터의 하루는 KST 17시(UTC-8 자정)에 시작하지만, 독자의 하루는
# 아침 8~11시 반이다. 야간(쿼터일 앞부분)에 전량을 소진하면 정작
# 조간 뉴스가 아침에 분석되지 못하므로, 야간에는 오전 몫을 남겨둔다.
NIGHT_START_KST = 17  # 쿼터 리셋 시각부터 (2026-08-12: 16 → 17 정정)
NIGHT_END_KST = 6     # 조간 수집 시작 전까지


def nightly_soft_limit(soft_limit: int, morning_reserve: int, kst_hour: int) -> int:
    """야간(KST 16시~다음날 6시)에 적용할 일일 소프트리밋.

    야간이면 오전 예약분(morning_reserve)을 뺀 상한을, 주간이면 원래
    상한을 돌려준다. 순수 함수 — 시각은 호출자가 넣는다.
    """
    if kst_hour >= NIGHT_START_KST or kst_hour < NIGHT_END_KST:
        return max(0, soft_limit - morning_reserve)
    return soft_limit
