"""모델별 요청 속도 제한기 (사용자 지시 2026-08-11).

무료 한도는 모델당 분당 15요청(RPM)인데 실측 사용률이 7/15(47%)였다.
고정 간격 sleep은 "생성이 5초 걸리든 8초 걸리든 무조건 N초 더 쉰다"라서
한도를 남긴 채 Actions 시간만 태운다. 이 제한기는 **직전 호출로부터
경과한 시간을 빼고 필요한 만큼만** 재우므로, 생성이 느린 날은 대기가 0이
되고 빠른 날에만 간격을 벌린다 — 한도 안에서 최대한 촘촘해진다.

모델마다 별도 인스턴스를 쓴다 (한도가 모델별로 따로 있다).
"""

from __future__ import annotations

import threading
import time

from .freshness import next_slot


class RateLimiter:
    """분당 target_rpm 이하를 보장하는 슬롯 배급기 (스레드 안전)."""

    def __init__(self, target_rpm: float) -> None:
        if target_rpm <= 0:
            raise ValueError("target_rpm은 0보다 커야 합니다")
        self.min_interval = 60.0 / target_rpm
        self._lock = threading.Lock()
        # 첫 호출은 즉시 나가야 하므로 충분히 과거로 초기화
        self._last = time.monotonic() - self.min_interval

    def acquire(self) -> float:
        """다음 슬롯을 받을 때까지 대기한다. 실제로 잔 시간을 반환."""
        with self._lock:
            now = time.monotonic()
            slot = next_slot(self._last, now, self.min_interval)
            self._last = slot
            wait = slot - now
        if wait > 0:
            time.sleep(wait)
            return wait
        return 0.0
