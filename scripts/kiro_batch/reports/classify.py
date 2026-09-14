"""보고서 로봇 관련성 pre-filter (스펙 §16 + 사용자 지시 2026-08-08).

2단계 키워드 체계 (사용자: "드론이 로봇에 들어와.. 중요성의 차이야...
로봇은 드론을 툴로 쓰는거야.. AI도 마찬가지고"):
- CORE: 로봇이 핵심 주제인 키워드 — 정상 우선순위로 분석
- TOOL: 드론·무인이동체·AI 등 로봇이 도구로 쓰는 영역 — 수집·분석은
  하되 우선순위를 낮춘다 (쿼터는 CORE부터). 최종 노출은 Gemini의
  robot_relevance 판정(DIRECT/RELATED만 공개)이 결정한다.

규칙 출처 (2026-09-08, 관리 기능): 아래 모듈 상수는 내장 기본값이다.
운영에서는 collect_reports가 시작할 때 configure_from_db(conn)로
keyword_rules(rule_set='report_tier', kind=core|tool)로 교체한다.
DB가 비었거나 못 읽으면 기본값을 그대로 쓴다.
"""

from __future__ import annotations

from ..keyword_rules import Rules, Term, compile_terms, load_rules, plain

# 포함 후보 (스펙 §16 목록 그대로 — 소문자 비교) — 내장 기본값
INCLUDE_KEYWORDS = [
    "로봇",
    "robot",
    "robotics",
    "로보틱스",
    "휴머노이드",
    "humanoid",
    "피지컬 ai",
    "피지컬ai",
    "physical ai",
    "physical intelligence",
    "embodied ai",
    "embodied intelligence",
    "체화 ai",
    "체화지능",
    "체화 지능",
    "협동로봇",
    "cobot",
    "매니퓰레이터",
    "manipulator",
    "로봇팔",
    "robot arm",
    "amr",
    "agv",
    "자율로봇",
    "서비스로봇",
    "물류로봇",
    "배송로봇",
    "의료로봇",
    "수술로봇",
    "재활로봇",
    "돌봄로봇",
    "농업로봇",
    "국방로봇",
    "재난로봇",
    "해양로봇",
    "수중로봇",
    "웨어러블 로봇",
    "외골격",
    "로봇 액추에이터",
    "robot actuator",
    "로봇 감속기",
    "gripper",
    "그리퍼",
    "slam",
    "hri",
    "human-robot interaction",
]


# 로봇이 도구로 쓰는 인접 영역 — 낮은 우선순위로 수집·분석 — 내장 기본값
TOOL_KEYWORDS = [
    "드론",
    "무인기",
    "uav",
    "무인항공",
    "무인이동체",
    "무인선",
    "무인수상정",
    "무인잠수정",
    "자율주행",
    "자율운항",
    "자율제조",
    "인공지능",
    "피지컬 인텔리전스",
    "스마트팩토리",
    "스마트공장",
    "스마트 공장",
    "스마트제조",
    "머신비전",
]

# 실제 판정에 쓰는 매처 — 기본값으로 시작, configure()로 교체
_core: list[Term] = compile_terms(plain(INCLUDE_KEYWORDS))
_tool: list[Term] = compile_terms(plain(TOOL_KEYWORDS))
_source = "내장 기본값"


def configure(rules: Rules | None) -> bool:
    """{core: [...], tool: [...]} 규칙으로 교체. core가 비어 있으면(None·0건)
    내장 기본값으로 되돌리고 False를 돌려준다."""
    global _core, _tool, _source
    if not rules or not rules.get("core"):
        _core = compile_terms(plain(INCLUDE_KEYWORDS))
        _tool = compile_terms(plain(TOOL_KEYWORDS))
        _source = "내장 기본값"
        return False
    _core = compile_terms(rules["core"], label="report_tier/core")
    _tool = compile_terms(rules.get("tool", []), label="report_tier/tool")
    _source = "DB"
    return True


def configure_from_db(conn) -> bool:
    """keyword_rules(report_tier)로 교체. 실패·0건이면 기본값 유지 (배치는 계속)."""
    ok = configure(load_rules(conn, "report_tier"))
    print(
        f"[classify] 보고서 계층 키워드 출처 {_source}: "
        f"core {len(_core)} · tool {len(_tool)}"
    )
    return ok


def reset() -> None:
    """내장 기본값으로 되돌린다 (테스트용)."""
    configure(None)


def keyword_tier(title: str, abstract: str | None = None) -> str | None:
    """'CORE'(로봇 핵심) | 'TOOL'(인접 도구 영역) | None(무관)."""
    text = f"{title} {abstract or ''}".lower()
    if any(t.find(text) for t in _core):
        return "CORE"
    if any(t.find(text) for t in _tool):
        return "TOOL"
    return None


def prefilter_pass(title: str, abstract: str | None = None) -> bool:
    """CORE 또는 TOOL 키워드가 하나라도 있으면 통과."""
    return keyword_tier(title, abstract) is not None


def matched_keywords(title: str, abstract: str | None = None) -> list[str]:
    text = f"{title} {abstract or ''}".lower()
    hits: set[str] = set()
    for t in [*_core, *_tool]:
        m = t.find(text)
        if m:
            hits.add(m)
    return sorted(hits)
