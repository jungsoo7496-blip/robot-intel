"""제목·본문 유사도와 클러스터 판정 보조 (FR-005, tasks §6.2).

Phase 1 클러스터 기준 (설계 §8.4):
    URL 동일
    OR (제목 유사도 높음 AND 발표일 차이 3일 이내 AND 기관·기업·정책명 일치)
"""

from __future__ import annotations

import hashlib
import re
from datetime import date

from rapidfuzz import fuzz

# [단독], (종합), 【속보】 등 매체 관례 표기 제거
_BRACKET_RE = re.compile(r"[\[\(【〈<][^\]\)】〉>]{0,12}[\]\)】〉>]")
_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\"'‘’“”·…‥,\.!?~ㆍ|/\\-]+")


def normalize_title(title: str) -> str:
    """비교용 제목 정규화: 괄호 표기·문장부호·공백 정리 + 소문자."""
    t = _BRACKET_RE.sub(" ", title or "")
    t = _PUNCT_RE.sub(" ", t)
    t = _WHITESPACE_RE.sub(" ", t)
    return t.strip().lower()


def title_similarity(a: str, b: str) -> float:
    """정규화된 제목의 유사도(0~100)."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    return fuzz.token_set_ratio(na, nb)


def content_hash(text: str) -> str:
    """본문 완전 중복 판정용 해시. 공백 차이는 무시한다."""
    normalized = _WHITESPACE_RE.sub(" ", (text or "").strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def within_days(a: date | None, b: date | None, max_days: int = 3) -> bool:
    """발표일 차이가 max_days 이내인지. 날짜를 모르면 보수적으로 True."""
    if a is None or b is None:
        return True
    return abs((a - b).days) <= max_days


def share_entity(entities_a: list[str], entities_b: list[str]) -> bool:
    """기관·기업·정책명 후보가 하나 이상 겹치는지."""
    set_a = {e.strip().lower() for e in entities_a if e and e.strip()}
    set_b = {e.strip().lower() for e in entities_b if e and e.strip()}
    return bool(set_a & set_b)


def is_same_event(
    title_a: str,
    title_b: str,
    date_a: date | None,
    date_b: date | None,
    entities_a: list[str],
    entities_b: list[str],
    threshold: int = 85,
) -> bool:
    """같은 사건을 다룬 콘텐츠인지 판정한다 (설계 §8.4)."""
    if title_similarity(title_a, title_b) < threshold:
        return False
    if not within_days(date_a, date_b, 3):
        return False
    return share_entity(entities_a, entities_b)
