"""어댑터 레지스트리 (스펙 §12) — adapter_key → 구현.

새 소스는 어댑터 구현 + 이 표에 등록 + Admin에서 채널 설정으로 붙인다.
DB·스키마·워크플로는 수정하지 않는다 (스펙 §46).
"""

from __future__ import annotations

from .alio import AlioAdapter
from .nanet import NanetAdapter
from .nkis import NkisAdapter
from .point import PointAdapter
from .prism import PrismAdapter
from .scienceon import ScienceonAdapter

# adapter_key → 생성자 (지연 생성 — 실행되는 채널의 어댑터만 만든다)
_ADAPTERS = {
    "point": PointAdapter,
    "prism": PrismAdapter,  # data.go.kr prism_v2 (PRISM_API_KEY 필요)
    "nanet": NanetAdapter,  # 국회도서관 — 자료검색 승인 대기 (키는 PRISM과 동일)
    "nkis": NkisAdapter,    # NKIS OpenAPI (NKIS_API_KEY — 2026-08-19 수신)
    "alio": AlioAdapter,    # ALIO 연구보고서 JSON (키 불필요, 서울 중계 경유)
    "scienceon": ScienceonAdapter,  # KISTI API Gateway (고정 IV 발견으로 해결)
}


def get_adapter(adapter_key: str):
    """어댑터 인스턴스를 만든다. 미구현 키는 None (해당 소스만 건너뜀)."""
    cls = _ADAPTERS.get(adapter_key)
    return cls() if cls else None
