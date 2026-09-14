"""스키마 v1.1 테스트 — KIRO 구조화 필드 (외부 리뷰 지시 14항).

Gemini 실호출 없이 fixture로 스키마 계약을 검증한다.
CASE A~E는 '모델이 이렇게 반환했을 때 시스템이 올바르게 수용/거부하는가'를 본다.
"""

import pytest

from kiro_batch.schemas import (
    KIRO_AXES,
    KIRO_CONTEXT_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    SchemaValidationError,
    build_search_text,
    validate_analysis,
)

BASE = {
    "is_robot_related": True,
    "relevance_reason": "로봇 정책 직접 관련",
    "display_title": "산업부, 휴머노이드 실증사업 발표",
    "one_line_summary": "산업부가 휴머노이드 실증사업을 발표했다.",
    "category": "정책",
    "region": "국내",
    "robot_field": "휴머노이드·피지컬 AI",
    "importance": "높음",
    "evidence_level": "강함",
    "kiro_relevance": "직접",
    "kiro_relevance_axes": ["정책·전략", "R&D 기획"],
    "kiro_relevance_reason": "국가 로봇 R&D 기획 지원 기능과 직접 연결되는 신규 사업",
    "kiro_watchpoints": ["세부 R&D 공고의 실증 과제 포함 여부"],
    "verified_facts": ["산업부가 실증사업을 발표했다"],
    "numbers_and_dates": [],
    "policy_meta": None,
    "ai_interpretation": "실증 중심 정책 기조가 강화되고 있다.",
    "kiro_implication": "실증 인프라 수요 확대 여부를 추적할 가치가 있다.",
    "limitations": "",
    "keywords": ["휴머노이드"],
}


def test_versions_bumped():
    assert SCHEMA_VERSION == "1.1"
    assert PROMPT_VERSION == "article_analysis_v2"
    assert KIRO_CONTEXT_VERSION == "kiro_public_context_v1"
    assert len(KIRO_AXES) == 8


def test_new_fields_parsed():
    analysis, status = validate_analysis(BASE)
    assert status == "PASS"
    assert analysis.kiro_relevance_axes == ["정책·전략", "R&D 기획"]
    assert analysis.kiro_watchpoints == ["세부 R&D 공고의 실증 과제 포함 여부"]


def test_invalid_axis_rejected():
    data = {**BASE, "kiro_relevance_axes": ["정책·전략", "휴머노이드"]}
    with pytest.raises(SchemaValidationError):
        validate_analysis(data)


def test_empty_watchpoints_allowed():
    data = {**BASE, "kiro_watchpoints": []}
    _, status = validate_analysis(data)
    assert status == "PASS"


def test_too_many_watchpoints_truncated_with_warn():
    data = {**BASE, "kiro_watchpoints": [f"항목{i}" for i in range(6)]}
    analysis, status = validate_analysis(data)
    assert status == "WARN"
    assert len(analysis.kiro_watchpoints) == 4


def test_direct_relevance_requires_reason():
    data = {**BASE, "kiro_relevance_reason": ""}
    with pytest.raises(SchemaValidationError):
        validate_analysis(data)


def test_missing_new_fields_default_empty():
    # v1 형식 응답(신규 필드 없음)도 파싱은 되어야 함 — 기본값 빈 배열
    data = {k: v for k, v in BASE.items()
            if k not in ("kiro_relevance_axes", "kiro_watchpoints")}
    data["kiro_relevance"] = "간접"  # 직접이면 reason 필수라 간접으로
    data["kiro_relevance_reason"] = ""
    analysis, _ = validate_analysis(data)
    assert analysis.kiro_relevance_axes == []
    assert analysis.kiro_watchpoints == []


def test_search_text_includes_new_fields():
    analysis, _ = validate_analysis(BASE)
    text = build_search_text(analysis)
    assert "R&D 기획" in text
    assert "세부 R&D 공고의 실증 과제 포함 여부" in text
    assert analysis.kiro_relevance_reason in text


# ── 대표 fixture (CASE A~E): 스키마가 각 유형을 올바르게 수용하는지 ──

def test_case_a_humanoid_rnd_direct():
    """CASE A: 휴머노이드 정부 R&D → 직접 + 정책·전략/R&D 기획 등."""
    analysis, status = validate_analysis(BASE)
    assert status == "PASS"
    assert analysis.kiro_relevance == "직접"
    assert set(analysis.kiro_relevance_axes) <= set(KIRO_AXES)


def test_case_b_service_robot_field_validation():
    """CASE B: 서비스로봇 현장 실증 → 직접 + 실증·시험평가 포함."""
    data = {
        **BASE,
        "robot_field": "서비스 로봇",
        "kiro_relevance_axes": ["실증·시험평가"],
        "kiro_relevance_reason": "ISO 18646 기반 서비스로봇 성능시험 역량과 직접 연결",
    }
    analysis, status = validate_analysis(data)
    assert status == "PASS"
    assert "실증·시험평가" in analysis.kiro_relevance_axes


def test_case_c_startup_launch_indirect_allowed():
    """CASE C: 스타트업 제품 출시 → 무조건 직접이 아니어도 유효."""
    data = {
        **BASE,
        "category": "산업",
        "kiro_relevance": "간접",
        "kiro_relevance_axes": ["기업지원·사업화"],
        "kiro_relevance_reason": "",  # 간접은 reason 없이도 허용
        "kiro_implication": "기업 시험평가 수요로 이어지는지 관찰할 가치가 있다.",
    }
    _, status = validate_analysis(data)
    assert status == "PASS"


def test_case_d_contest_low_minimal_implication():
    """CASE D: 대학 경진대회 수상 → 낮음 + 빈 시사점·빈 축 허용."""
    data = {
        **BASE,
        "kiro_relevance": "낮음",
        "kiro_relevance_axes": [],
        "kiro_relevance_reason": "",
        "kiro_watchpoints": [],
        "kiro_implication": "",
    }
    _, status = validate_analysis(data)
    assert status == "PASS"


def test_case_e_non_robot():
    """CASE E: 로봇 무관 일반 AI 뉴스 → is_robot_related=false."""
    data = {"is_robot_related": False, "relevance_reason": "로봇 무관"}
    analysis, status = validate_analysis(data)
    assert analysis.is_robot_related is False
    assert status == "PASS"


def test_prompt_files_wired():
    """article_analysis_v2에 컨텍스트 플레이스홀더가 있고 실제 주입되는지."""
    from pathlib import Path

    prompts = Path(__file__).parent.parent.parent / "prompts"
    template = (prompts / "article_analysis_v2.txt").read_text(encoding="utf-8")
    context = (prompts / "context" / "kiro_public_context_v1.md").read_text(
        encoding="utf-8"
    )
    assert "{{KIRO_PUBLIC_CONTEXT}}" in template
    assert "전문생산기술연구기관" in context
    injected = template.replace("{{KIRO_PUBLIC_CONTEXT}}", context)
    assert "KOLAS" in injected
    assert "{{KIRO_PUBLIC_CONTEXT}}" not in injected
