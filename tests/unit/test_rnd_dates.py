"""NTIS 수집 KST 날짜 계산 (외부 리뷰 P2-3).

GitHub runner는 UTC라 date.today()를 쓰면 KST 자정~오전 9시 사이
접수중/접수예정 판정이 하루 어긋난다 — kst_date는 항상 KST 날짜를 준다.
"""

from datetime import date, datetime, timezone

from kiro_batch.collect_rnd import kst_date, match_keywords


def test_utc_evening_is_next_kst_day():
    # UTC 8/7 16:00 = KST 8/8 01:00
    assert kst_date(datetime(2026, 8, 7, 16, 0, tzinfo=timezone.utc)) == date(2026, 8, 8)


def test_utc_morning_same_kst_day():
    # UTC 8/8 02:00 = KST 8/8 11:00
    assert kst_date(datetime(2026, 8, 8, 2, 0, tzinfo=timezone.utc)) == date(2026, 8, 8)


def test_month_boundary():
    # UTC 8/31 20:00 = KST 9/1 05:00 — 월 경계
    assert kst_date(datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc)) == date(2026, 9, 1)


def test_year_boundary():
    # UTC 12/31 16:00 = KST 1/1 01:00 — 연도 경계
    assert kst_date(datetime(2026, 12, 31, 16, 0, tzinfo=timezone.utc)) == date(2027, 1, 1)


def test_naive_datetime_treated_as_utc():
    assert kst_date(datetime(2026, 12, 31, 16, 0)) == date(2027, 1, 1)


def test_keyword_match_title_and_body():
    # 제목에 로봇이 없어도 본문에서 잡힌다 (P1-3 취지의 회귀 방지)
    # 2026-08-09 확장: 'ai' 약어도 영숫자 경계 기준으로 함께 잡힌다
    assert match_keywords(
        "첨단제조 신규과제 공고", "피지컬 AI 기반 협업 시스템 개발"
    ) == ["ai", "피지컬 ai"]
    assert match_keywords("Physical AI 플랫폼 구축", None) == ["ai", "physical ai"]
    assert match_keywords("일반 바이오 과제", "세포 배양") == []


def test_keyword_abbr_boundaries():
    # 'AI기반'처럼 붙여 써도 잡히고, chain·maintain 류에는 오탐하지 않는다
    assert "ai" in match_keywords("AI기반 자율 제조 시스템", None)
    assert match_keywords("Supply chain maintenance 사업", None) == []
    # 인접 분야 확장 키워드 (2026-08-09)
    assert match_keywords("상지 재활 보조 의료기기 시험 플랫폼", None)
    assert match_keywords("물류창고 화재 감지 순찰 시스템", None)
    assert match_keywords("용접 공정 디지털트윈 시뮬레이터", None)
