"""정책 메타데이터 정규화 (FR-010, tasks §9.4).

AI가 반환한 policy_meta를 policy_details 행으로 변환한다.
금액은 원문 텍스트를 보존하고, 숫자 환산에 실패하면 숫자 필드를 null로 둔다.
"""

from __future__ import annotations

import re
from datetime import date

# "총 300억원", "1조 2,000억 원", "45억5000만원" 등 한국어 금액 표기
_AMOUNT_RE = re.compile(
    r"(?:(?P<jo>[\d,\.]+)\s*조)?\s*"
    r"(?:(?P<eok>[\d,\.]+)\s*억)?\s*"
    r"(?:(?P<man>[\d,\.]+)\s*만)?\s*"
    r"(?:(?P<won>[\d,]+))?\s*원"
)

_UNIT = {"jo": 1_0000_0000_0000, "eok": 1_0000_0000, "man": 1_0000}


def parse_krw_amount(text: str | None) -> int | None:
    """한국어 금액 표기를 원 단위 정수로 환산한다. 실패 시 None."""
    if not text:
        return None
    m = _AMOUNT_RE.search(text.replace(" ", " "))
    if not m:
        return None

    total = 0
    matched_any = False
    for group, unit in _UNIT.items():
        raw = m.group(group)
        if raw:
            try:
                total += int(float(raw.replace(",", "")) * unit)
                matched_any = True
            except ValueError:
                return None
    won_raw = m.group("won")
    if won_raw and not matched_any:
        # "3,000,000원"처럼 단위 없는 원 표기
        try:
            total += int(won_raw.replace(",", ""))
            matched_any = True
        except ValueError:
            return None
    return total if matched_any and total > 0 else None


def parse_iso_date(value: str | None) -> date | None:
    """YYYY-MM-DD 형식만 신뢰한다. 그 외 형식·불명확한 값은 None."""
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def normalize_policy_meta(policy_meta: dict | None) -> dict | None:
    """AI policy_meta → policy_details 컬럼 dict. 정책이 아니면 None."""
    if not policy_meta or not isinstance(policy_meta, dict):
        return None

    def _text(key: str) -> str | None:
        v = policy_meta.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else None

    def _list(key: str) -> list[str] | None:
        v = policy_meta.get(key)
        if isinstance(v, list):
            items = [str(x).strip() for x in v if str(x).strip()]
            return items or None
        return None

    budget_text = _text("budget_text")
    budget_amount = policy_meta.get("budget_amount_krw")
    if not isinstance(budget_amount, (int, float)) or budget_amount <= 0:
        # AI 환산 값이 없거나 이상하면 로컬 파서로 재시도, 실패 시 null 유지
        budget_amount = parse_krw_amount(budget_text)
    else:
        budget_amount = int(budget_amount)

    row = {
        "policy_name": _text("policy_name"),
        "project_name": _text("project_name"),
        "ministries": _list("ministries"),
        "organizations": _list("organizations"),
        "budget_text": budget_text,
        "budget_amount_krw": budget_amount,
        "project_start_date": parse_iso_date(policy_meta.get("project_start_date")),
        "project_end_date": parse_iso_date(policy_meta.get("project_end_date")),
        "support_targets": _list("support_targets"),
        "announcement_status": _text("announcement_status"),
        "application_deadline": parse_iso_date(policy_meta.get("application_deadline")),
        "target_region": _text("target_region"),
    }

    # 모든 값이 비어 있으면 정책 상세 행을 만들지 않는다
    if not any(v is not None for v in row.values()):
        return None
    return row
