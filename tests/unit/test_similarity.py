"""제목 유사도·클러스터 판정 단위 테스트 (FR-005, tasks §18.1)."""

from datetime import date

from kiro_batch.similarity import (
    is_same_event,
    normalize_title,
    title_similarity,
    within_days,
)


def test_normalize_strips_brackets():
    assert normalize_title("[단독] 삼성, 로봇 출시") == normalize_title("삼성, 로봇 출시")


def test_same_press_release_high_similarity():
    a = "산업부, 휴머노이드 로봇 실증사업 300억 투자"
    b = "산업부 휴머노이드 로봇 실증사업에 300억 투자한다"
    assert title_similarity(a, b) >= 85


def test_different_events_low_similarity():
    a = "산업부, 휴머노이드 로봇 실증사업 300억 투자"
    b = "테슬라 옵티머스 2세대 공개"
    assert title_similarity(a, b) < 85


def test_within_days():
    assert within_days(date(2026, 8, 1), date(2026, 8, 3), 3)
    assert not within_days(date(2026, 8, 1), date(2026, 8, 10), 3)
    # 날짜 미상은 보수적으로 허용
    assert within_days(None, date(2026, 8, 1), 3)


def test_same_event_requires_entity_overlap():
    a_title = "산업부, 로봇 실증사업 발표"
    b_title = "산업부 로봇 실증사업을 발표했다"
    assert is_same_event(
        a_title, b_title,
        date(2026, 8, 1), date(2026, 8, 2),
        ["산업통상자원부"], ["산업통상자원부", "KIRO"],
    )
    # 기관이 전혀 겹치지 않으면 다른 사건으로 유지
    assert not is_same_event(
        a_title, b_title,
        date(2026, 8, 1), date(2026, 8, 2),
        ["산업통상자원부"], ["과학기술정보통신부"],
    )
