"""보고서 수집 공통 모델·어댑터 인터페이스 (스펙 §12).

소스별 API/HTML 세부사항은 각 어댑터 안에 가둔다 — 밖으로는
ReportCandidate 목록만 나온다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


@dataclass
class ReportCandidate:
    """어댑터가 발견한 보고서 후보 1건 (occurrence가 될 원료)."""

    source_key: str
    channel_key: str
    external_id: str
    title: str
    detail_url: str
    institution: str | None = None
    authors: list[str] = field(default_factory=list)
    published_date: date | None = None
    published_year: int | None = None
    source_report_type: str | None = None
    abstract: str | None = None
    source_keywords: list[str] = field(default_factory=list)
    # access checker가 검사할 후보 URL들 (상세페이지에서 추출한 원문 링크 등)
    candidate_download_urls: list[str] = field(default_factory=list)
    # 소스가 공식 온라인 뷰어를 제공함을 어댑터가 확인한 경우 (→ VIEW_ONLY)
    viewer_hint: bool = False


@dataclass
class ChannelConfig:
    """DB report_source_channels 한 행의 실행 설정 (어댑터 입력)."""

    source_key: str
    channel_key: str
    query: str | None
    config: dict
    max_pages: int
    max_items: int
    request_interval_ms: int
    credential: str | None  # credential_key_name으로 env에서 읽은 값


class ReportAdapter(Protocol):
    """소스 어댑터 공통 인터페이스."""

    def fetch_recent(self, channel: ChannelConfig) -> list[ReportCandidate]:
        """채널 설정에 따라 최근 자료를 수집한다."""
        ...

    def fetch_detail(self, candidate: ReportCandidate) -> ReportCandidate:
        """필요 시 상세 조회로 초록·원문 후보 URL을 보강한다."""
        ...
