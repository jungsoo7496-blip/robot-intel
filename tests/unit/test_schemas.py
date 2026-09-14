"""AI 출력 스키마 검증 테스트 (FR-007, tasks §18.1)."""

import pytest

from kiro_batch.schemas import (
    SchemaValidationError,
    build_search_text,
    validate_analysis,
)

VALID = {
    "is_robot_related": True,
    "relevance_reason": "로봇 정책 직접 관련",
    "display_title": "산업부, 휴머노이드 실증사업 발표",
    "one_line_summary": "산업부가 300억원 규모 휴머노이드 실증사업을 발표했다.",
    "category": "정책",
    "region": "국내",
    "robot_field": "휴머노이드·피지컬 AI",
    "importance": "높음",
    "evidence_level": "강함",
    "kiro_relevance": "직접",
    "kiro_relevance_axes": ["정책·전략"],
    "kiro_relevance_reason": "국가 로봇 R&D 기획 지원 기능과 직접 연결",
    "verified_facts": ["산업부가 실증사업을 발표했다"],
    "numbers_and_dates": [
        {"label": "예산", "value": "300억원", "source_basis": "원문"}
    ],
    "policy_meta": None,
    "ai_interpretation": "실증 중심 정책 기조가 강화되고 있다.",
    "kiro_implication": "실증 인프라 수요 증가가 예상된다.",
    "limitations": "세부 공고 일정은 미확인.",
    "keywords": ["휴머노이드", "실증"],
}


def test_valid_analysis_passes():
    analysis, status = validate_analysis(VALID)
    assert status == "PASS"
    assert analysis.category == "정책"


def test_invalid_enum_rejected():
    data = {**VALID, "category": "경제"}
    with pytest.raises(SchemaValidationError):
        validate_analysis(data)


def test_empty_verified_facts_rejected():
    """verified_facts가 비어 있으면 게시 불가 (설계 §13)."""
    data = {**VALID, "verified_facts": []}
    with pytest.raises(SchemaValidationError):
        validate_analysis(data)


def test_not_robot_related_allows_empty_fields():
    data = {
        "is_robot_related": False,
        "relevance_reason": "로봇 무관 콘텐츠",
    }
    analysis, status = validate_analysis(data)
    assert analysis.is_robot_related is False
    assert status == "PASS"


def test_excessive_numbers_warn():
    data = {
        **VALID,
        "numbers_and_dates": [
            {"label": f"n{i}", "value": str(i), "source_basis": "원문"}
            for i in range(12)
        ],
    }
    _, status = validate_analysis(data)
    assert status == "WARN"


def test_search_text_contains_key_fields():
    analysis, _ = validate_analysis(VALID)
    text = build_search_text(analysis)
    assert "휴머노이드" in text
    assert "실증" in text
    assert analysis.one_line_summary in text
