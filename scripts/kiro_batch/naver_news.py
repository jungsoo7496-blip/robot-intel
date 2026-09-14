"""네이버 뉴스 검색 API 수집기 — NAVER API HUB (사용자 요청).

- 애플리케이션 등록: 네이버클라우드 NAVER API HUB (ncloud.com)
- 인증 헤더: X-NCP-APIGW-API-KEY-ID / X-NCP-APIGW-API-KEY
- adapter_config: {"query": "로봇", "display": 100}
- link 대신 originallink(언론사 원문)를 저장해 본문 추출이 가능하게 한다.
- 호출 한도는 플랫폼 안내 기준 월 775,000회(한시 무료 제공) —
  배치당 검색어 3회 호출이라 여유가 매우 크다. 실제 한도는 콘솔에서 확인.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx

_API_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
_TAG_RE = re.compile(r"</?b>|&quot;|&amp;|&lt;|&gt;|&apos;")

_TAG_MAP = {"&quot;": '"', "&amp;": "&", "&lt;": "<", "&gt;": ">", "&apos;": "'"}

# 페이지당 100건이 API 상한. 3페이지 = 최신 300건을 본다 (2026-08-15).
# 종전에는 1페이지(100건)만 봤는데, 병목 검색어("로봇")의 100건 창이 평일
# 약 10.5시간이라 04:30 수집 폐지(밤 공백 7.6시간)와 주말 감축(공백 16시간)
# 을 견딜 여유가 없었고, 실제로 08-11·08-12에 창이 꽉 차 유실이 확인됐다.
# 300건 창은 약 31시간. 추가 비용은 검색어당 API 호출 2회(~1초)뿐이다 —
# 이미 수집된 항목은 배치 guid 검사가 묶음 쿼리 한 번으로 걸러낸다.
_PAGE_SIZE = 100
_MAX_PAGES = 3


def _clean(text: str) -> str:
    """네이버 API가 붙이는 <b> 강조 태그·HTML 엔티티 제거."""
    def repl(m: re.Match) -> str:
        return _TAG_MAP.get(m.group(0), "")
    return _TAG_RE.sub(repl, text or "").strip()


@dataclass
class NaverItem:
    url: str
    title: str
    summary: str | None
    published_at: datetime | None


def _fetch_page(
    client_id: str,
    client_secret: str,
    query: str,
    start: int,
    display: int,
    timeout: float,
) -> list[dict]:
    response = httpx.get(
        _API_URL,
        params={
            "query": query,
            "display": display,
            "start": start,
            "sort": "date",
            "format": "json",
        },
        headers={
            "X-NCP-APIGW-API-KEY-ID": client_id,
            "X-NCP-APIGW-API-KEY": client_secret,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json().get("items", [])


def collect_naver_news(
    client_id: str,
    client_secret: str,
    query: str,
    display: int = _PAGE_SIZE * _MAX_PAGES,
    timeout: float = 20.0,
) -> list[NaverItem]:
    """네이버 뉴스 검색 결과를 최신순으로 가져온다 (최대 3페이지 300건)."""
    if not client_id or not client_secret:
        raise RuntimeError(
            "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET이 설정되지 않았습니다."
        )

    items: list[NaverItem] = []
    seen_urls: set[str] = set()
    remaining = min(display, _PAGE_SIZE * _MAX_PAGES)
    start = 1
    while remaining > 0:
        page_size = min(remaining, _PAGE_SIZE)
        entries = _fetch_page(
            client_id, client_secret, query, start, page_size, timeout
        )
        for entry in entries:
            # originallink가 언론사 원문 — 없으면 네이버 링크로 대체
            url = entry.get("originallink") or entry.get("link")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            published = None
            if entry.get("pubDate"):
                try:
                    published = parsedate_to_datetime(entry["pubDate"])
                except (ValueError, TypeError):
                    published = None
            items.append(
                NaverItem(
                    url=url,
                    title=_clean(entry.get("title", "")),
                    summary=_clean(entry.get("description", "")) or None,
                    published_at=published,
                )
            )
        # 페이지가 덜 채워졌으면 더 없다 — 추가 호출은 낭비
        if len(entries) < page_size:
            break
        start += page_size
        remaining -= page_size
    return items
