"""조간 신선도 계산 — DB·IO 없는 순수 함수 (2026-08-11).

사용자 제보: "아침에 보는 기사가 다 어제 것이다."
원인은 분석 큐가 FIFO(먼저 들어온 순)라, 아침 배치가 언제나 어제 저녁분부터
처리하고 예산이 끝나 버리는 것이었다. 여기 모은 함수들이 그 판정 기준이다.

호(號) 경계 계산은 오프바이원이 나기 쉽고 눈으로 안 잡히므로(설계 검토
중에도 실제로 한 번 틀렸다) DB 없이 테스트 가능한 순수 함수로 격리한다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

EDITION_HOUR = 8  # 조간 마감·판갈이 (KST) — src/lib/daily.ts의 EDITION_HOUR와 일치

# 큐 정렬에서 "신선하다"고 보는 창. 이 안이면 원문 발행 순(최신 우선),
# 밖이면 오래 기다린 순(FIFO)으로 처리해 기아를 막는다.
FRESH_WINDOW_HOURS = 36

# 신선 창 밖의 대기분에 배치 예산의 일부를 반드시 배분한다 (기아 방지).
# 신선도 정렬만 쓰면 유입 > 처리인 동안 오래된 job이 영원히 안 뽑힌다.
STARVATION_QUOTA_RATIO = 0.2


def edition_of(t_kst: datetime) -> date:
    """원문 시각이 속하는 호. 창은 [D-1 08:00, D 08:00) KST."""
    return (t_kst - timedelta(hours=EDITION_HOUR)).date() + timedelta(days=1)


def current_edition_at(t_kst: datetime) -> date:
    """t 시점에 '최신호'로 열려 있는 호 (edition_of와 하루 차이)."""
    return (t_kst - timedelta(hours=EDITION_HOUR)).date()


def is_live_edition(source_time_kst: datetime, now_kst: datetime) -> bool:
    """이 기사를 지금 게시하면 독자가 '최신호'에서 볼 수 있는가."""
    return edition_of(source_time_kst) > current_edition_at(now_kst)


def clamp_source_time(
    published_at: datetime | None, now: datetime
) -> datetime | None:
    """수집원이 준 미래 발행시각을 현재로 상한 클램프.

    일부 매체가 발행일을 앞당겨 표기해, 그대로 두면 display_date가 미래가 되어
    조간 창(display_date < 마감)에서 빠진다 — 가장 비싼 조간 배치를 쓰고도
    화면에 안 나오는 자료가 된다. None(시각 미상)은 그대로 둔다.
    """
    if published_at is None:
        return None
    return min(published_at, now)


def freshness_tier(priority: int) -> int:
    """정렬용 우선순위 계층. p40(일반)과 p45(저확신)를 한 계층으로 묶는다.

    실측상 p45가 대기의 68%인데 그중 상당수가 시스템에서 가장 신선한
    네이버 수집분이었다. p40 뒤에 줄 세우면 신선한 재료가 통째로 굶는다.
    정부·공공(10)·전문매체(30)·백필(60)의 구분은 그대로 둔다.
    """
    return (priority // 10) * 10


def age_hours(source_time: datetime, now: datetime) -> float:
    return (now - source_time).total_seconds() / 3600


def is_fresh(source_time: datetime | None, now: datetime) -> bool:
    """큐 정렬에서 신선 그룹으로 볼지. 시각 미상은 신선하지 않은 쪽으로."""
    if source_time is None:
        return False
    return 0 <= age_hours(source_time, now) <= FRESH_WINDOW_HOURS


def starvation_slots(batch_capacity: int) -> int:
    """배치에서 '오래 기다린 것'에 반드시 배정할 건수 (최소 1건)."""
    if batch_capacity <= 0:
        return 0
    return max(1, int(batch_capacity * STARVATION_QUOTA_RATIO))


def batch_budget_seconds(kst_hour: int, base: int) -> int:
    """시간대별 배치 시간 예산 (사용자 지시 2026-08-11).

    독자는 아침 8~11시 반에만 본다. 그러니 밤사이 쌓인 것을 아침 전에
    털어내는 조간 배치에 시간을 몰아주고, 낮·저녁 배치는 그날 새로 들어온
    분량만 따라가면 된다. **하루 총량은 그대로 두고 배분만 바꾼다** —
    Actions 분을 늘리지 않는다.
      조간(03~08시) 540초 ×2회 + 주간(08~14시) 360초 + 막차(14~17시,
      드레인이 별도 상향) + 야간(17~03시) 240초 ≈ 기존 420초×5회와 동일.

    구간을 크론 지연폭보다 넓게 잡는 것이 요점이다. 예산을 '실행된 시각'으로
    정하는데 GitHub 크론은 +38~137분 밀려서 뜨므로, 구간이 좁으면 배치가
    의도한 슬롯의 예산을 못 받는다. 실제로 08-12에 10:10 슬롯이 12:27에
    떠서 180초(=51건)만 받았다 — 지연이 예산까지 깎는 이중 손해였다.
    """
    if 3 <= kst_hour < 8:
        return 540  # 조간 — 밤사이 유입을 판갈이 전에 턴다 (2회 = 약 340건)
    if 8 <= kst_hour < 14:
        return 360  # 열람 시간대(08~11:30) 갱신 + 지연분 흡수
    if 14 <= kst_hour < 17:
        return base  # 막차 배치 — should_drain이 걸리면 더 늘어난다
    return 240


def next_slot(last_slot: float, now: float, min_interval: float) -> float:
    """다음 호출을 허용할 시각 (단조 증가 — 호출 간격 하한을 보장).

    분당 요청 한도를 지키면서 최대한 촘촘히 쓰기 위한 계산. 직전 슬롯이
    과거면 지금 바로, 아직 미래면 그 시각까지 기다린다.
    """
    return max(now, last_slot + min_interval)


def should_drain(
    hours_until_reset: float, pending: int, batch_cap: int
) -> bool:
    """쿼터 리셋 직전 막차 소진 여부.

    기존 조건은 `KST 15시에 시작`이었는데, 크론이 실제로 +13~95분 지연돼
    16시 이후에 시작하는 일이 잦아 한 번도 발동하지 못했다. 시각 대신
    '리셋까지 남은 시간'과 '태울 백로그가 있는지'로 판정한다.
    """
    return 0 < hours_until_reset <= 3.0 and pending >= batch_cap
