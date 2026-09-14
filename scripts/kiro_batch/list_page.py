"""공개 목록 페이지 수집 어댑터 (FR-002, tasks §5.2).

임의 사이트 범용 크롤러가 아니라, 수집원별 CSS 선택자를
sources.adapter_config(JSONB)로 정의하는 사전 정의 방식이다.

adapter_config 예시 (korea.kr 정책뉴스):
{
  "item_selector": "a[href*='policyNewsView.do']",
  "title_selector": "strong",
  "summary_selector": "span.lead",
  "base_url": "https://www.korea.kr"
}
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; KIRO-RobotIntel/0.1)"}


@dataclass
class ListItem:
    url: str
    title: str | None
    summary: str | None


def collect_list_page(
    page_url: str,
    adapter_config: dict,
    timeout: float = 20.0,
) -> list[ListItem]:
    """목록 페이지에서 기사 링크·제목·요약을 추출한다."""
    item_selector = adapter_config.get("item_selector")
    if not item_selector:
        raise ValueError("adapter_config.item_selector가 필요합니다")
    title_selector = adapter_config.get("title_selector")
    summary_selector = adapter_config.get("summary_selector")
    base_url = adapter_config.get("base_url") or page_url

    response = httpx.get(
        page_url, headers=_HEADERS, timeout=timeout, follow_redirects=True
    )
    response.raise_for_status()
    tree = HTMLParser(response.text)

    items: list[ListItem] = []
    seen: set[str] = set()
    for node in tree.css(item_selector):
        href = node.attributes.get("href")
        if not href:
            link_node = node.css_first("a[href]")
            href = link_node.attributes.get("href") if link_node else None
        if not href:
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)

        title = None
        if title_selector:
            t = node.css_first(title_selector)
            title = t.text(strip=True) if t else None
        if not title:
            title = node.text(strip=True, separator=" ")[:200] or None

        summary = None
        if summary_selector:
            s = node.css_first(summary_selector)
            summary = s.text(strip=True) if s else None

        items.append(ListItem(url=url, title=title, summary=summary))
    return items
