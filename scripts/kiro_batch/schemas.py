"""AI 기사 분석 출력 스키마와 검증 (설계 §12~13, tasks §8.3, §9.2).

TypeScript 쪽 동일 스키마: src/types/analysis.ts
스키마를 바꾸면 SCHEMA_VERSION을 올리고 양쪽을 함께 수정한다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

SCHEMA_VERSION = "1.1"
PROMPT_VERSION = "article_analysis_v2"
KIRO_CONTEXT_VERSION = "kiro_public_context_v1"

# KIRO 업무축 (브로슈어 공개 기능 기준, 8개로 제한 — 기술분야는 robot_field 담당)
KIRO_AXES = [
    "정책·전략",
    "R&D 기획",
    "연구개발",
    "실증·시험평가",
    "기업지원·사업화",
    "인재양성",
    "협력·생태계",
    "전략방향",
]
MAX_WATCHPOINTS = 4

CATEGORIES = ["정책", "산업", "기술"]
REGIONS = ["국내", "미국", "중국", "일본", "유럽", "기타"]
# 서비스/물류 분리 (0012). '서비스·물류 로봇'은 v1 legacy — 새 분석에는 미사용.
ROBOT_FIELDS = [
    "휴머노이드·피지컬 AI",
    "제조·산업용 로봇",
    "서비스 로봇",
    "물류 로봇",
    "의료·돌봄 로봇",
    "농업 로봇",
    "국방·재난 로봇",
    "해양·특수환경 로봇",
    "핵심 부품·소프트웨어",
    "기타",
]
IMPORTANCE = ["높음", "보통", "낮음"]
EVIDENCE_LEVELS = ["강함", "보통", "약함"]
KIRO_RELEVANCE = ["직접", "간접", "낮음"]


class NumberOrDate(BaseModel):
    label: str
    value: str
    source_basis: str = "원문"


class PolicyMeta(BaseModel):
    policy_name: str | None = None
    project_name: str | None = None
    ministries: list[str] | None = None
    organizations: list[str] | None = None
    budget_text: str | None = None
    budget_amount_krw: float | None = None
    project_start_date: str | None = None
    project_end_date: str | None = None
    support_targets: list[str] | None = None
    announcement_status: str | None = None
    application_deadline: str | None = None
    target_region: str | None = None


class ArticleAnalysis(BaseModel):
    """Gemini 기사 분석 구조화 출력 (설계 §12)."""

    is_robot_related: bool
    relevance_reason: str = ""
    display_title: str = ""
    one_line_summary: str = ""
    category: str | None = None
    region: str | None = None
    robot_field: str | None = None
    importance: str | None = None
    evidence_level: str | None = None
    kiro_relevance: str | None = None
    # v1.1: KIRO 관련성 구조화 (브로슈어 공개 컨텍스트 기반)
    kiro_relevance_axes: list[str] = Field(default_factory=list)
    kiro_relevance_reason: str = ""
    kiro_watchpoints: list[str] = Field(default_factory=list)
    verified_facts: list[str] = Field(default_factory=list)
    numbers_and_dates: list[NumberOrDate] = Field(default_factory=list)
    policy_meta: PolicyMeta | None = None
    ai_interpretation: str = ""
    kiro_implication: str = ""
    limitations: str = ""
    keywords: list[str] = Field(default_factory=list)


class SchemaValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_analysis(data: dict) -> tuple[ArticleAnalysis, str]:
    """파싱 + 게시 조건 검증. (analysis, validation_status)를 반환한다.

    validation_status:
      PASS — 게시 가능
      WARN — 게시 가능하나 주의 표시 (숫자 과다 등)
      FAIL — 게시 불가 (관련 콘텐츠인데 필수 정보 부족)
    """
    try:
        analysis = ArticleAnalysis.model_validate(data)
    except ValidationError as e:
        raise SchemaValidationError(
            [f"{err['loc']}: {err['msg']}" for err in e.errors()]
        ) from e

    errors: list[str] = []

    if analysis.is_robot_related:
        # 게시 조건: enum 필수 (설계 §13)
        if analysis.category not in CATEGORIES:
            errors.append(f"category 값 오류: {analysis.category!r}")
        if analysis.region not in REGIONS:
            errors.append(f"region 값 오류: {analysis.region!r}")
        if analysis.robot_field not in ROBOT_FIELDS:
            errors.append(f"robot_field 값 오류: {analysis.robot_field!r}")
        if analysis.importance not in IMPORTANCE:
            errors.append(f"importance 값 오류: {analysis.importance!r}")
        if analysis.evidence_level not in EVIDENCE_LEVELS:
            errors.append(f"evidence_level 값 오류: {analysis.evidence_level!r}")
        if analysis.kiro_relevance not in KIRO_RELEVANCE:
            errors.append(f"kiro_relevance 값 오류: {analysis.kiro_relevance!r}")
        # v1.1: 허용되지 않은 업무축 거부
        invalid_axes = [a for a in analysis.kiro_relevance_axes if a not in KIRO_AXES]
        if invalid_axes:
            errors.append(f"kiro_relevance_axes 값 오류: {invalid_axes!r}")
        # '직접' 판정에는 구체적 근거 필수
        if analysis.kiro_relevance == "직접" and not analysis.kiro_relevance_reason.strip():
            errors.append("kiro_relevance=직접인데 kiro_relevance_reason이 비어 있음")
        if not analysis.display_title.strip():
            errors.append("display_title 누락")
        if not analysis.one_line_summary.strip():
            errors.append("one_line_summary 누락")
        # verified_facts가 비어 있으면 게시하지 않음 (설계 §13)
        facts = [f for f in analysis.verified_facts if f and f.strip()]
        if not facts:
            errors.append("verified_facts가 비어 있음")

    else:
        # 로봇 무관 판정이면 위 enum 검사를 건너뛰는데, 저장은 그대로 하므로
        # AI가 목록에 없는 값(예: category='기타')을 주면 DB CHECK 제약이
        # 거부해 SAVE_ERROR로 5회 재시도 끝에 FAILED가 된다 (2026-08-11 실측).
        # 게시하지 않는 분석이므로 분류값은 비워서 저장한다.
        analysis.category = analysis.category if analysis.category in CATEGORIES else None
        analysis.region = analysis.region if analysis.region in REGIONS else None
        analysis.robot_field = (
            analysis.robot_field if analysis.robot_field in ROBOT_FIELDS else None
        )
        analysis.importance = (
            analysis.importance if analysis.importance in IMPORTANCE else None
        )
        analysis.evidence_level = (
            analysis.evidence_level
            if analysis.evidence_level in EVIDENCE_LEVELS
            else None
        )
        analysis.kiro_relevance = (
            analysis.kiro_relevance if analysis.kiro_relevance in KIRO_RELEVANCE else None
        )

    if errors:
        raise SchemaValidationError(errors)

    status = "PASS"
    # 원문에 없는 숫자·날짜 과다 생성 경고 (설계 §13) — 개수 기반 1차 경고
    if len(analysis.numbers_and_dates) > 10:
        status = "WARN"
    # watchpoints는 최대 4개 — 초과분은 잘라내고 경고 (v1.1)
    if len(analysis.kiro_watchpoints) > MAX_WATCHPOINTS:
        analysis.kiro_watchpoints = analysis.kiro_watchpoints[:MAX_WATCHPOINTS]
        status = "WARN"
    return analysis, status


def build_search_text(analysis: ArticleAnalysis) -> str:
    """published_items.search_text 구성 (설계 §11)."""
    parts: list[str] = [
        analysis.display_title,
        analysis.one_line_summary,
        *analysis.verified_facts,
        analysis.ai_interpretation,
        analysis.kiro_implication,
        analysis.kiro_relevance_reason,
        *analysis.kiro_watchpoints,
        *analysis.kiro_relevance_axes,
        *analysis.keywords,
    ]
    if analysis.policy_meta:
        pm = analysis.policy_meta
        parts += [
            pm.policy_name or "",
            pm.project_name or "",
            *(pm.ministries or []),
            *(pm.organizations or []),
        ]
    return "\n".join(p.strip() for p in parts if p and p.strip())
