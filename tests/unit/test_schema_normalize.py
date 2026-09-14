"""로봇 무관 분석의 분류값 정규화 (2026-08-11 SAVE_ERROR 회귀 방지).

AI가 is_robot_related=false로 판정하면 enum 검사를 건너뛰는데, 저장은
그대로 하므로 DB CHECK 제약(category ∈ 정책/산업/기술)이 거부해
5회 재시도 끝에 FAILED가 됐다. 게시 안 하는 분석이니 비워서 저장한다.
"""

from kiro_batch.schemas import validate_analysis


def base(**over):
    d = {
        "is_robot_related": False,
        "relevance_reason": "로봇과 무관한 일반 기사",
        "display_title": "제목",
        "one_line_summary": "요약",
        "verified_facts": [],
    }
    d.update(over)
    return d


class TestNonRobotNormalization:
    def test_invalid_category_becomes_none(self):
        analysis, status = validate_analysis(base(category="기타"))
        assert analysis.category is None
        assert status == "PASS"

    def test_invalid_robot_field_becomes_none(self):
        analysis, _ = validate_analysis(base(robot_field="없는분야"))
        assert analysis.robot_field is None

    def test_valid_values_survive(self):
        analysis, _ = validate_analysis(
            base(category="산업", robot_field="물류 로봇", importance="보통")
        )
        assert analysis.category == "산업"
        assert analysis.robot_field == "물류 로봇"
        assert analysis.importance == "보통"

    def test_all_enum_fields_normalized(self):
        analysis, _ = validate_analysis(
            base(
                category="X", region="Y", robot_field="Z",
                importance="W", evidence_level="V", kiro_relevance="U",
            )
        )
        assert (
            analysis.category
            is analysis.region
            is analysis.robot_field
            is analysis.importance
            is analysis.evidence_level
            is analysis.kiro_relevance
            is None
        )


class TestRobotRelatedStillStrict:
    def test_invalid_category_still_rejected_when_published(self):
        import pytest

        from kiro_batch.schemas import SchemaValidationError

        with pytest.raises(SchemaValidationError):
            validate_analysis(
                base(
                    is_robot_related=True,
                    category="기타",  # 게시 대상은 여전히 엄격
                    region="국내",
                    robot_field="물류 로봇",
                    importance="보통",
                    evidence_level="보통",
                    kiro_relevance="간접",
                    verified_facts=["사실1"],
                )
            )
