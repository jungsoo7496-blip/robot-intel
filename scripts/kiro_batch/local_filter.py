"""로컬 1차 관련성 필터 (FR-004, tasks §6.1).

Gemini 호출 전에 명백한 비관련·저가치 콘텐츠를 제외해 무료 호출량을 아낀다.
- PASS: AI 분석 대상
- LOW_PRIORITY: 불확실 — 폐기하지 않고 낮은 우선순위로 AI 큐에 보낸다
- EXCLUDE: 명백한 비관련·광고·채용 등

규칙 출처 (2026-09-08, 관리 기능):
- 운영은 keyword_rules 테이블(rule_set='news_filter')에서 읽는다 → from_db()
- filter_rules.json은 내장 기본값 — DB가 비었거나 못 읽을 때의 폴백이자
  테스트용이다 → LocalFilter() / LocalFilter(rules_path)
- 본문 최소 길이는 app_settings 'filter_min_body_length' (없으면 JSON 값)
같은 판정 로직이 관리 화면의 테스터(admin/keywords/actions.ts)에 TS로
옮겨져 있다 — 순서를 바꾸면 그쪽도 함께 바꿀 것.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from .keyword_rules import (
    Rules,
    Term,
    compile_terms,
    load_rules,
    load_setting_int,
    plain,
    regex,
)

_RULES_PATH = Path(__file__).parent / "filter_rules.json"
DEFAULT_MIN_BODY_LENGTH = 200
MIN_BODY_LENGTH_SETTING = "filter_min_body_length"


@dataclass(frozen=True)
class FilterResult:
    status: str          # PASS | LOW_PRIORITY | EXCLUDE
    reason: str


def load_json_rules(rules_path: Path | str = _RULES_PATH) -> dict:
    with open(rules_path, encoding="utf-8") as f:
        return json.load(f)


def rules_from_json(raw: dict) -> Rules:
    """filter_rules.json 구조를 keyword_rules와 같은 {kind: [(term, is_regex)]}로."""
    return {
        "robot": plain(raw.get("robot_keywords", [])),
        "strong": plain(raw.get("strong_keywords", [])),
        "exclude": regex(raw.get("exclude_patterns", [])),
        "event_only": regex(raw.get("event_only_patterns", [])),
    }


class LocalFilter:
    def __init__(self, rules_path: Path | str = _RULES_PATH):
        """JSON 파일 규칙으로 생성 (폴백·테스트용)."""
        raw = load_json_rules(rules_path)
        self._configure(
            rules_from_json(raw),
            int(raw.get("min_body_length", DEFAULT_MIN_BODY_LENGTH)),
            source="JSON",
        )

    @classmethod
    def from_rules(
        cls, rules: Rules, min_body_length: int = DEFAULT_MIN_BODY_LENGTH,
    ) -> "LocalFilter":
        """규칙 dict({kind: [(term, is_regex)]})를 직접 주입해 생성."""
        obj = cls.__new__(cls)
        obj._configure(rules, int(min_body_length), source="rules")
        return obj

    @classmethod
    def from_db(cls, conn, rules_path: Path | str = _RULES_PATH) -> "LocalFilter":
        """keyword_rules(news_filter) + app_settings로 생성.

        robot 키워드가 한 건도 없으면(0건·조회 실패) 필터가 전부를 제외해
        버리므로 JSON 기본값 전체로 폴백한다 — 배치는 어떤 경우에도 멈추지 않는다.
        """
        raw = load_json_rules(rules_path)
        default_min = int(raw.get("min_body_length", DEFAULT_MIN_BODY_LENGTH))
        min_len = load_setting_int(conn, MIN_BODY_LENGTH_SETTING, default_min)

        db_rules = load_rules(conn, "news_filter")
        if db_rules.get("robot"):
            rules, source = db_rules, "DB"
        else:
            rules, source = rules_from_json(raw), "JSON"
            print(
                "[local_filter] DB에 뉴스 필터 규칙이 없어 filter_rules.json "
                "기본값을 사용합니다",
                file=sys.stderr,
            )

        obj = cls.__new__(cls)
        obj._configure(rules, min_len, source=source)
        print(
            f"[local_filter] 규칙 출처 {source}: robot {len(obj.robot_terms)} · "
            f"strong {len(obj.strong_terms)} · exclude {len(obj.exclude_terms)} · "
            f"event_only {len(obj.event_only_terms)} · 본문 최소 {obj.min_body_length}자"
        )
        return obj

    def _configure(self, rules: Rules, min_body_length: int, *, source: str) -> None:
        self.source = source
        self.min_body_length: int = min_body_length
        self.robot_terms: list[Term] = compile_terms(
            rules.get("robot", []), label="news_filter/robot"
        )
        self.strong_terms: list[Term] = compile_terms(
            rules.get("strong", []), label="news_filter/strong"
        )
        self.exclude_terms: list[Term] = compile_terms(
            rules.get("exclude", []), label="news_filter/exclude"
        )
        self.event_only_terms: list[Term] = compile_terms(
            rules.get("event_only", []), label="news_filter/event_only"
        )

    def evaluate(self, title: str, body: str | None) -> FilterResult:
        title = (title or "").strip()
        body = (body or "").strip()
        title_lower = title.lower()
        body_lower = body.lower()
        combined_lower = f"{title_lower}\n{body_lower}"

        # 1) 명백한 광고·채용·주가·행사 패턴
        for term in self.exclude_terms:
            if term.find(combined_lower):
                return FilterResult("EXCLUDE", f"제외 패턴 일치: {term.term}")

        title_hits = sum(1 for t in self.robot_terms if t.find(title_lower))
        body_hits = sum(1 for t in self.robot_terms if t.find(body_lower))
        strong_in_title = any(t.find(title_lower) for t in self.strong_terms)

        # 2) 로봇 키워드가 전혀 없으면 명백한 비관련
        if title_hits == 0 and body_hits == 0:
            return FilterResult("EXCLUDE", "로봇 관련 키워드 없음")

        # 3) 단순 행사 안내: 로봇 키워드가 있어도 낮은 우선순위로 보류
        for term in self.event_only_terms:
            if term.find(title_lower):
                return FilterResult("LOW_PRIORITY", f"단순 행사 안내 추정: {term.term}")

        # 4) 본문이 너무 짧으면 불확실 — AI가 최종 판단
        if len(body) < self.min_body_length:
            return FilterResult(
                "LOW_PRIORITY", f"본문 길이 부족({len(body)} < {self.min_body_length})"
            )

        # 5) 제목에 핵심 키워드가 있으면 확실한 통과
        if strong_in_title or title_hits >= 1:
            return FilterResult("PASS", "제목에 로봇 키워드 존재")

        # 6) 본문에만 언급 — 부수적 언급 가능성이 있어 불확실 처리
        if body_hits >= 3:
            return FilterResult("PASS", f"본문 로봇 키워드 {body_hits}회")
        return FilterResult("LOW_PRIORITY", f"본문 로봇 키워드 {body_hits}회 — 관련성 불확실")
