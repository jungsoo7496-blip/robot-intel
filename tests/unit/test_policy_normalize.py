"""정책 메타데이터 정규화 테스트 (FR-010, tasks §18.1)."""

from datetime import date

from kiro_batch.policy_normalize import (
    normalize_policy_meta,
    parse_iso_date,
    parse_krw_amount,
)


def test_parse_simple_eok():
    assert parse_krw_amount("총 300억원") == 30_000_000_000


def test_parse_jo_and_eok():
    assert parse_krw_amount("1조 2,000억 원") == 1_200_000_000_000


def test_parse_eok_and_man():
    assert parse_krw_amount("45억5000만원") == 4_550_000_000


def test_parse_plain_won():
    assert parse_krw_amount("3,000,000원") == 3_000_000


def test_parse_failure_returns_none():
    assert parse_krw_amount("예산 미정") is None
    assert parse_krw_amount(None) is None
    assert parse_krw_amount("") is None


def test_iso_date_strict():
    assert parse_iso_date("2026-01-01") == date(2026, 1, 1)
    # 불명확한 형식은 임의 해석하지 않는다 (AIR-002)
    assert parse_iso_date("2026년 1월") is None
    assert parse_iso_date("2026") is None


def test_normalize_full_meta():
    row = normalize_policy_meta({
        "policy_name": "지능형 로봇 기본계획",
        "project_name": "휴머노이드 실증",
        "ministries": ["산업통상자원부"],
        "organizations": [],
        "budget_text": "총 300억원",
        "budget_amount_krw": None,
        "project_start_date": "2026-01-01",
        "project_end_date": "2030-12-31",
        "support_targets": ["로봇 기업"],
        "announcement_status": "발표",
        "application_deadline": None,
        "target_region": "전국",
    })
    assert row is not None
    # AI가 숫자 환산을 못 했으면 로컬 파서가 보완한다
    assert row["budget_amount_krw"] == 30_000_000_000
    assert row["project_start_date"] == date(2026, 1, 1)
    assert row["ministries"] == ["산업통상자원부"]
    assert row["organizations"] is None  # 빈 배열은 null


def test_normalize_non_policy_returns_none():
    assert normalize_policy_meta(None) is None
    assert normalize_policy_meta({}) is None


def test_budget_parse_failure_keeps_text_only():
    row = normalize_policy_meta({"budget_text": "예산 규모 미정"})
    assert row is not None
    assert row["budget_text"] == "예산 규모 미정"
    assert row["budget_amount_krw"] is None
