"""POINT(국립중앙도서관 정책정보포털) 어댑터 (스펙 §12·13).

공식 Open API — 인증키 불필요 (사용자 제공 공식 안내문 기준):
- 목록: https://policy.nl.go.kr/openapi/searchKwdApi.do
    kwd, category(jonghab|chamgo|digital), sort, desc, pageIndex, pageSize
- 상세: https://policy.nl.go.kr/openapi/searchDetailApi.do?rec_key=...
    → policy_url(상세페이지), brm(정부기능분류), country_type 등

원문 링크는 상세 JSON에 없으므로 상세 HTML에서 후보를 추출한다.
"""

from __future__ import annotations

import re
import time
from datetime import date

import httpx

from .base import ChannelConfig, ReportCandidate
from .relay import relay_mounts

_LIST_URL = "https://policy.nl.go.kr/openapi/searchKwdApi.do"
_DETAIL_URL = "https://policy.nl.go.kr/openapi/searchDetailApi.do"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; KIRO-RobotIntel/0.1)"}
_PAGE_SIZE = 20

# 상세 HTML에서 원문/다운로드 후보로 볼 링크 패턴
_FILE_LINK_RE = re.compile(
    r'href="([^"]+(?:\.pdf|\.hwpx?|download|fileDown|Down\.do)[^"]*)"',
    re.IGNORECASE,
)
_ORIGIN_LINK_RE = re.compile(
    r'<a[^>]+href="(https?://[^"]+)"[^>]*>[^<]*(?:원문|바로가기|원문보기)',
)
# "온라인보기" — 온라인 뷰어 (실측: openviewer_popup('CNTS-...') 또는 index_object(...,'V',...))
_VIEWER_RE = re.compile(
    r"openviewer_popup\('CNTS-[\w-]+'\)|index_object\('[^']+',\s*'V'"
)
# "다운로드" 버튼 (실측: fn_egov_downFile('370973','199214')
#  → /cmmn/FileDown.do?atchFileId=370973&fileSn=199214, common.js에서 확인)
_DOWNFILE_RE = re.compile(r"fn_egov_downFile\('(\d+)','(\d+)'\)")


def _clean_title(raw: str) -> str:
    t = re.sub(r"<[^>]+>", "", raw)  # 검색어 하이라이트 <font> 제거
    return re.sub(r"\s+", " ", t).strip()


def _parse_pub_year(raw: str) -> tuple[int | None, str | None]:
    """pub_year 실측 포맷: '20240923'(일)·'202412'(월)·'2024'(연).

    (published_year, published_date ISO) — 날짜는 8자리일 때만 채운다
    (월·연 단위를 1일로 지어내지 않는다)."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) >= 8:
        y, m, d = digits[:4], digits[4:6], digits[6:8]
        if "01" <= m <= "12" and "01" <= d <= "31":
            return int(y), f"{y}-{m}-{d}"
    if len(digits) >= 4:
        year = int(digits[:4])
        if 1900 <= year <= 2100:
            return year, None
    return None, None


class PointAdapter:
    def __init__(self) -> None:
        # policy.nl.go.kr는 relay.RELAYED_HOSTS에 포함 — Actions에서는 국내 리전을
        # 경유한다 (2026-08-19).
        self._client = httpx.Client(
            headers=_HEADERS, timeout=15.0, mounts=relay_mounts()
        )

    def fetch_recent(self, channel: ChannelConfig) -> list[ReportCandidate]:
        category = channel.config.get("category", "chamgo")
        out: list[ReportCandidate] = []
        for page in range(1, channel.max_pages + 1):
            resp = self._client.get(
                _LIST_URL,
                params={
                    "kwd": channel.query or "로봇",
                    "category": category,
                    "sort": "ipub_year",
                    "desc": "desc",
                    "pageIndex": page,
                    "pageSize": _PAGE_SIZE,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            rows = data.get("result") or []
            for row in rows:
                rec_key = row.get("rec_key")
                title = _clean_title(row.get("title") or "")
                if not rec_key or not title:
                    continue
                year, date_iso = _parse_pub_year(str(row.get("pub_year") or ""))
                out.append(
                    ReportCandidate(
                        source_key=channel.source_key,
                        channel_key=channel.channel_key,
                        external_id=rec_key,
                        title=title,
                        detail_url=(
                            "https://policy.nl.go.kr/search/searchDetail.do"
                            f"?rec_key={rec_key}"
                        ),
                        institution=(row.get("publisher") or None),
                        authors=[a.strip() for a in (row.get("author") or "").split(",") if a.strip()],
                        published_date=date.fromisoformat(date_iso) if date_iso else None,
                        published_year=year,
                        source_report_type=category,
                    )
                )
                if len(out) >= channel.max_items:
                    return out
            if len(rows) < _PAGE_SIZE:
                break
            time.sleep(channel.request_interval_ms / 1000)
        return out

    def fetch_detail(self, candidate: ReportCandidate) -> ReportCandidate:
        """상세 JSON으로 메타데이터 보강 + 상세 HTML에서 원문 후보 추출."""
        try:
            resp = self._client.get(
                _DETAIL_URL, params={"rec_key": candidate.external_id}
            )
            resp.raise_for_status()
            d = resp.json()
            if d.get("subject"):
                candidate.abstract = str(d["subject"])[:2000]
            if d.get("brm"):
                candidate.source_keywords = [
                    s.strip() for s in str(d["brm"]).split(",") if s.strip()
                ]
        except httpx.HTTPError:
            pass

        try:
            page = self._client.get(candidate.detail_url, follow_redirects=True)
            if page.status_code == 200:
                html = page.text
                urls: list[str] = []
                for m in _DOWNFILE_RE.finditer(html):
                    urls.append(
                        "https://policy.nl.go.kr/cmmn/FileDown.do"
                        f"?atchFileId={m.group(1)}&fileSn={m.group(2)}"
                    )
                for m in _FILE_LINK_RE.finditer(html):
                    url = m.group(1)
                    if url.startswith("/"):
                        url = "https://policy.nl.go.kr" + url
                    if url.startswith("http"):
                        urls.append(url)
                for m in _ORIGIN_LINK_RE.finditer(html):
                    urls.append(m.group(1))
                # 순서 유지 중복 제거
                candidate.candidate_download_urls = list(dict.fromkeys(urls))[:5]
                # "온라인보기" 뷰어 제공 여부 → 파일 후보 없어도 VIEW_ONLY
                candidate.viewer_hint = bool(_VIEWER_RE.search(html))
        except httpx.HTTPError:
            pass
        return candidate

    def close(self) -> None:
        self._client.close()
