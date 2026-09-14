"""국회도서관 OpenAPI 어댑터 (data.go.kr 9720000, 사용자 가이드 PDF 기준).

- 상세검색: GET /searchservice/detail?dbname=&search=&option=&pageno=&displaylines=
    dbname: '웹자료'(공공기관 정책자료)·'세미나자료' 등
    search: "자료명,로봇" 형태 / option: "발행년도,2021|발행년도,2026|원문유무,1"
    응답: XML <response><record><item><name>..</name><value>..</value></item>...
- 상세정보: GET /detailinfoservice/detail?contorlno=제어번호 (승인 확인 済)

원문은 국회전자도서관(dl.nanet.go.kr) 열람 페이지 — 직접 파일 URL은 없어
VIEW_ONLY/METADATA_ONLY 위주다. 가치는 '여러 기관 정책자료를 한 곳에서'.
"""

from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from .base import ChannelConfig, ReportCandidate

_BASE = "https://apis.data.go.kr/9720000"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; KIRO-RobotIntel/0.1)"}
_PAGE_SIZE = 50
# 국회전자도서관 검색 페이지 (제어번호 검색) — 사용자에게 보여줄 링크
_DETAIL_PAGE = "https://dl.nanet.go.kr/search/searchInnerDetail.do?controlNo="


_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _clean_value(raw: str) -> str:
    """검색어 하이라이트(<font color=red>)가 이스케이프된 채 실려 온다 — 제거."""
    return re.sub(r"\s+", " ", _TAG_STRIP_RE.sub("", raw or "")).strip()


def _parse_records(xml_text: str) -> tuple[list[dict[str, str]], int]:
    """<recode><item><name>/<value> 반복 구조 → dict 목록 + total.

    실측(2026-08-25): 실제 응답의 반복 요소는 <record>가 아니라
    **<recode>** 다 — API 자체의 오타가 스펙이 됐다. 가이드 문서 표기였던
    <record>도 함께 수용한다 (언젠가 고칠 수 있으니).
    """
    root = ET.fromstring(xml_text)
    total_el = root.find(".//total")
    total_raw = re.sub(r"\D", "", total_el.text or "") if total_el is not None else ""
    records = []
    for rec in root.findall(".//recode") + root.findall(".//record"):
        row: dict[str, str] = {}
        for item in rec.findall("item"):
            name = (item.findtext("name") or "").strip()
            value = (item.findtext("value") or "").strip()
            if name:
                row[name] = value
        if row:
            records.append(row)
    return records, int(total_raw) if total_raw else 0


def _split_title_author(raw: str) -> tuple[str, list[str]]:
    """'자료명/저자사항' = "제목 / 저자" 형태 분리."""
    parts = raw.split("/", 1)
    title = parts[0].strip()
    authors = []
    if len(parts) > 1:
        author = re.sub(r"(지음|저|편|작성)$", "", parts[1].strip()).strip()
        if author:
            authors = [a.strip() for a in re.split(r"[,;]", author) if a.strip()]
    return title, authors


class NanetAdapter:
    def __init__(self) -> None:
        self._client = httpx.Client(headers=_HEADERS, timeout=25.0)

    def fetch_recent(self, channel: ChannelConfig) -> list[ReportCandidate]:
        if not channel.credential:
            raise RuntimeError("NANET용 데이터포털 키 없음 (channel.credential)")
        self._last_credential = channel.credential  # fetch_detail에서 재사용
        dbname = channel.config.get("dbname", "웹자료")
        year_from = str(channel.config.get("year_from", datetime.now(timezone.utc).year - 5))
        year_to = str(channel.config.get("year_to", datetime.now(timezone.utc).year))
        query = channel.query or "로봇"

        out: list[ReportCandidate] = []
        page = 1
        while page <= channel.max_pages:
            resp = self._client.get(
                _BASE + "/searchservice/detail",
                params={
                    "ServiceKey": channel.credential,
                    "pageno": page,
                    "displaylines": _PAGE_SIZE,
                    "dbname": dbname,
                    "search": f"자료명,{query}",
                    "option": f"발행년도,{year_from}|발행년도,{year_to}",
                },
            )
            resp.raise_for_status()
            if "SERVICE_KEY" in resp.text[:400]:
                raise RuntimeError("국회도서관 자료검색 미승인 키 (심의 대기?)")
            records, total = _parse_records(resp.text)
            if not records:
                break
            for row in records:
                control_no = row.get("제어번호")
                # 실측 필드명(2026-08-25): 자료명·저자명·발행자·발행년도·키워드.
                # (가이드의 '자료명/저자사항'·'발행년'은 실제 응답에 없다)
                raw_title = row.get("자료명") or row.get("자료명/저자사항") or ""
                if not control_no or not raw_title:
                    continue
                title = _clean_value(raw_title)
                # "한글 제목 ... / 영문 부제" 형태 — 한글 쪽만 대표 제목으로
                if " / " in title:
                    title = title.split(" / ", 1)[0].strip()
                authors = [
                    a for a in [_clean_value(row.get("저자명") or "")] if a
                ]
                year_raw = re.sub(
                    r"\D", "", row.get("발행년도") or row.get("발행년") or ""
                )[:4]
                keywords = [
                    k.strip()
                    for k in _clean_value(row.get("키워드") or "").split()
                    if len(k.strip()) >= 2
                ][:10]
                out.append(
                    ReportCandidate(
                        source_key=channel.source_key,
                        channel_key=channel.channel_key,
                        external_id=control_no,
                        title=title,
                        detail_url=_DETAIL_PAGE + control_no,
                        institution=_clean_value(row.get("발행자") or "") or None,
                        authors=authors,
                        published_year=int(year_raw) if len(year_raw) == 4 else None,
                        source_report_type=dbname,
                        source_keywords=keywords,
                        candidate_download_urls=[],
                        # 원문DB유무 Y = 국회전자도서관 뷰어에서 원문 열람 가능
                        viewer_hint=(row.get("원문DB유무") or "").strip().upper()
                        in ("Y", "1"),
                    )
                )
                if len(out) >= channel.max_items:
                    return out
            if page * _PAGE_SIZE >= total:
                break
            page += 1
            time.sleep(channel.request_interval_ms / 1000)
        # 뷰어 힌트: 채널 config로 제어 (원문DB유무는 항목별이라 상세에서 보강)
        return out

    def fetch_detail(self, candidate: ReportCandidate) -> ReportCandidate:
        """상세정보조회로 초록·키워드 보강 (승인 확인된 detailinfoservice).

        주의(2026-08-25 실측): 자료검색과 상세정보가 서로 다른 data.go.kr
        키로 승인돼 있다 — 자료검색은 신규 키(DATA_GO_KR_API_KEY, 채널
        credential), 상세정보는 예전 키(NANET_DETAIL_API_KEY)만 통과한다.
        상세 키가 없으면 검색 키로 시도한다 (언젠가 통합 승인될 수 있음).
        """
        key = os.environ.get("NANET_DETAIL_API_KEY") or self._last_credential
        if not key:
            return candidate
        try:
            resp = self._client.get(
                _BASE + "/detailinfoservice/detail",
                params={"ServiceKey": key, "contorlno": candidate.external_id},
            )
            resp.raise_for_status()
            records, _ = _parse_records(resp.text)
        except (httpx.HTTPError, ET.ParseError):
            return candidate
        if records:
            row = records[0]
            abstract = row.get("초록") or row.get("내용") or ""
            if abstract:
                candidate.abstract = abstract[:2000]
            kw = row.get("키워드") or ""
            if kw:
                candidate.source_keywords = [
                    s.strip() for s in re.split(r"[,;]", kw) if s.strip()
                ][:10]
            if (row.get("원문DB유무") or row.get("원문유무")) in ("1", "Y"):
                candidate.viewer_hint = True
        return candidate

    _last_credential: str | None = None

    def close(self) -> None:
        self._client.close()
