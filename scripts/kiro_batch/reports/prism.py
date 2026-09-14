"""PRISM(온-나라 정책연구) 어댑터 — data.go.kr prism_v2 공식 API.

행정안전부_정책연구 과제정보 (사용자 발급 키, 2026-08-08 실측):
- 목록: GET /getResearchList_v2?start_date&end_date&numOfRows&pageNo
    연구기간이 [start_date, end_date]에 포함되는 과제만 반환 (검색어 없음)
- 상세: GET /getResearchDetail_v2?research_id
    → 개요(research_outline)·목차·주제어·계약정보 + 연구보고서 PDF 직접 URL

검색어 파라미터가 없으므로 로봇 관련 선별은 어댑터 안에서 제목 프리필터로
한다 (연 2,000건+ 목록을 그대로 반환하면 max_items 캡이 로봇 과제를
잘라먹는다). 개발계정 트래픽 일 1,000콜 — 목록 ~25콜 + 상세 소량이라 여유.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone

import httpx

from .base import ChannelConfig, ReportCandidate
from .classify import prefilter_pass

_BASE = "https://apis.data.go.kr/1741000/prism_v2"
_PAGE_SIZE = 100
_DETAIL_PAGE = "https://www.prism.go.kr/homepage/asmt/"

_TAG_RE = re.compile(r"<br\s*/?>|<[^>]+>")


def _clean_html(raw: str | None) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", raw or "")).strip()


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 8:
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            return None
    return None


class PrismAdapter:
    def __init__(self) -> None:
        self._client = httpx.Client(timeout=30.0)

    def fetch_recent(self, channel: ChannelConfig) -> list[ReportCandidate]:
        if not channel.credential:
            raise RuntimeError("PRISM_API_KEY 없음 (channel.credential)")
        self._last_credential = channel.credential  # fetch_detail에서 재사용
        years_back = int(channel.config.get("years_back", 1))
        this_year = datetime.now(timezone.utc).year
        out: list[ReportCandidate] = []

        for year in range(this_year, this_year - years_back - 1, -1):
            page = 1
            while page <= channel.max_pages:
                resp = self._client.get(
                    _BASE + "/getResearchList_v2",
                    params={
                        "serviceKey": channel.credential,
                        "start_date": f"{year}0101",
                        "end_date": f"{year}1231",
                        "numOfRows": _PAGE_SIZE,
                        "pageNo": page,
                        "type": "json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                rows = data.get("research") or []
                if isinstance(rows, dict):
                    rows = [rows]
                for row in rows:
                    rid = row.get("research_id")
                    name = (row.get("research_name") or "").strip()
                    if not rid or not name:
                        continue
                    # 검색어 API가 없으므로 여기서 로봇 관련만 선별
                    if not prefilter_pass(name):
                        continue
                    year_raw = str(row.get("issued_year") or "")
                    out.append(
                        ReportCandidate(
                            source_key=channel.source_key,
                            channel_key=channel.channel_key,
                            external_id=rid,
                            title=name,
                            detail_url=_DETAIL_PAGE + rid,
                            institution=(row.get("organ_name") or None),
                            authors=[
                                a.strip()
                                for a in (row.get("researcher_name") or "").split(",")
                                if a.strip()
                            ],
                            published_year=int(year_raw)
                            if year_raw.isdigit()
                            else year,
                            source_report_type="정책연구보고서",
                            source_keywords=[
                                s.strip()
                                for s in (row.get("biz_name") or "").split(",")
                                if s.strip()
                            ],
                        )
                    )
                    if len(out) >= channel.max_items:
                        return out
                total = int(data.get("totalCount") or 0)
                if page * _PAGE_SIZE >= total:
                    break
                page += 1
                time.sleep(channel.request_interval_ms / 1000)
        return out

    def fetch_detail(self, candidate: ReportCandidate) -> ReportCandidate:
        """상세로 초록·주제어·연구보고서 파일 URL 보강."""
        key = self._last_credential  # fetch_recent에서 보관 (DB에는 저장 안 함)
        if not key:
            return candidate
        try:
            resp = self._client.get(
                _BASE + "/getResearchDetail_v2",
                params={
                    "serviceKey": key,
                    "research_id": candidate.external_id,
                    "type": "json",
                },
            )
            resp.raise_for_status()
            d = resp.json()
        except (httpx.HTTPError, ValueError):
            return candidate

        research = d.get("research") or {}
        report = d.get("reportInfo") or {}

        outline = _clean_html(research.get("research_outline"))
        summary = _clean_html(report.get("summary"))
        candidate.abstract = (summary or outline)[:2000] or None

        end = _parse_date(research.get("research_end_date"))
        if end and not candidate.published_date:
            candidate.published_date = end

        keyword = _clean_html(report.get("keyword"))
        if keyword:
            candidate.source_keywords = [
                s.strip() for s in re.split(r"[,;·]", keyword) if s.strip()
            ][:10]

        urls = report.get("url")
        if isinstance(urls, dict):
            urls = [urls]
        files = []
        for u in urls or []:
            fu = (u or {}).get("file_url")
            if fu:
                # 연구보고서를 평가결과서 등보다 앞에
                priority = 0 if "연구보고서" in str(u.get("file_type") or "") else 1
                files.append((priority, fu))
        candidate.candidate_download_urls = [
            fu for _, fu in sorted(files, key=lambda t: t[0])
        ][:5]
        return candidate

    _last_credential: str | None = None

    def close(self) -> None:
        self._client.close()
