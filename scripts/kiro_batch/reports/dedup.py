"""Cross-source canonical dedup (스펙 §8).

같은 보고서가 POINT/NKIS/PRISM에 동시에 있어도 Gemini는 한 번만 부른다.
원칙: **잘못 합치는 것보다 중복 2개가 낫다** — 과도한 fuzzy merge 금지.
Phase 1 fingerprint = 정규화 제목 + 정규화 기관 + 발행연도의 해시.
"""

from __future__ import annotations

import hashlib
import re


def normalize_title(title: str) -> str:
    """비교용 제목 정규화 — 공백·괄호 부제·기호 차이를 무시한다."""
    t = title.strip().lower()
    t = re.sub(r"[\(\[（【].*?[\)\]）】]", " ", t)  # 괄호 부제 제거
    t = re.sub(r"[^\w가-힣]+", "", t)  # 기호·공백 제거
    return t


def normalize_institution(institution: str | None) -> str:
    if not institution:
        return ""
    t = institution.strip().lower()
    t = re.sub(r"[\(\[（].*?[\)\]）]", "", t)  # 약칭 괄호 제거
    t = re.sub(r"[^\w가-힣]+", "", t)
    return t


def document_fingerprint(
    title: str, institution: str | None, published_year: int | None
) -> str:
    """canonical Document 식별자 — 강한 식별자(DOI 등)가 없을 때의 기본.

    기관 또는 연도가 다르면 다른 문서로 본다 (보수적).
    """
    key = "|".join(
        [normalize_title(title), normalize_institution(institution),
         str(published_year or "")]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def analysis_input_hash(
    title: str,
    institution: str | None,
    abstract: str | None,
    source_report_type: str | None,
    keywords: list[str] | None,
    published_date: str | None,
) -> str:
    """동일 입력 재분석 방지용 해시 (스펙 §27)."""
    key = "|".join(
        [
            title.strip(),
            (institution or "").strip(),
            (abstract or "").strip()[:2000],
            (source_report_type or "").strip(),
            ",".join(sorted(keywords or [])),
            published_date or "",
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
