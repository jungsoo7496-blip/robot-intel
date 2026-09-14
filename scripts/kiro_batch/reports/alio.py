"""ALIO(공공기관 경영정보 공개시스템) 연구보고서 어댑터 (2026-08-19).

공공기관 370곳의 의무공시 연구보고서. 키·로그인 불필요.
화면(researchList.do)은 Vue 앱이라 정적 수집이 안 되고, 그 앱이 쓰는
JSON 엔드포인트를 직접 호출한다 (브라우저 네트워크 실측):
- 목록: GET /occasional/findResearchList.json?type=title&word=검색어&pageNo=N
    → data.result[] (10건/페이지), data.page.totalPage
- 상세: GET /occasional/findResearchDtl.json?seq=<seq>
    → data.researchDtl.content(초록), originalPrivateNm(공개/비공개),
      refrUrl(외부 원문), data.fileList[].fileNo(첨부)
- 파일: /download/download.json?fileNo=<fileNo> (상세 화면 JS 실측)

원문 비공개 문서도 제목·초록·비공개 사유는 공개라 METADATA_ONLY로 수집한다.
alio.go.kr는 relay.RELAYED_HOSTS에 포함 — Actions에서는 국내 리전을 경유한다
(로컬은 직접).
"""

from __future__ import annotations

import re
import time
from datetime import date

import httpx

from .base import ChannelConfig, ReportCandidate
from .relay import relay_mounts

_BASE = "https://www.alio.go.kr"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; KIRO-RobotIntel/0.1)"}
_PAGE_SIZE = 10  # 서버 고정


def _parse_date(raw: str | None) -> date | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) >= 8:
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            return None
    return None


class AlioAdapter:
    def __init__(self) -> None:
        self._client = httpx.Client(
            headers=_HEADERS, timeout=20.0, mounts=relay_mounts()
        )

    def fetch_recent(self, channel: ChannelConfig) -> list[ReportCandidate]:
        out: list[ReportCandidate] = []
        for page in range(1, channel.max_pages + 1):
            resp = self._client.get(
                _BASE + "/occasional/findResearchList.json",
                params={
                    "type": channel.config.get("search_type", "title"),
                    "word": channel.query or "로봇",
                    "pageNo": page,
                },
            )
            resp.raise_for_status()
            data = resp.json().get("data") or {}
            rows = data.get("result") or []
            for row in rows:
                seq = str(row.get("seq") or "").strip()
                title = re.sub(r"\s+", " ", str(row.get("rtitle") or "")).strip()
                if not seq or not title:
                    continue
                published = _parse_date(
                    row.get("publishDt") or row.get("disclosureResnDt")
                )
                author = str(row.get("author") or "").strip()
                out.append(
                    ReportCandidate(
                        source_key=channel.source_key,
                        channel_key=channel.channel_key,
                        external_id=seq,
                        title=title,
                        detail_url=f"{_BASE}/occasional/researchDtl.do?seq={seq}",
                        institution=(row.get("pname") or "").strip() or None,
                        authors=[author] if author else [],
                        published_date=published,
                        published_year=published.year if published else None,
                        source_report_type="공공기관 연구보고서",
                    )
                )
                if len(out) >= channel.max_items:
                    return out
            total_page = int((data.get("page") or {}).get("totalPage") or 1)
            if page >= total_page:
                break
            time.sleep(channel.request_interval_ms / 1000)
        return out

    def fetch_detail(self, candidate: ReportCandidate) -> ReportCandidate:
        try:
            resp = self._client.get(
                _BASE + "/occasional/findResearchDtl.json",
                params={"seq": candidate.external_id},
            )
            resp.raise_for_status()
            data = resp.json().get("data") or {}
        except (httpx.HTTPError, ValueError):
            return candidate

        dtl = data.get("researchDtl") or {}
        content = re.sub(r"\s+", " ", str(dtl.get("content") or "")).strip()
        if content:
            candidate.abstract = content[:2000]

        # 원문 비공개면 다운로드 후보를 만들지 않는다 → METADATA_ONLY 판정
        if str(dtl.get("originalPrivateNm") or "") == "비공개":
            reason = re.sub(
                r"\s+", " ", str(dtl.get("originalPrivateContent") or "")
            ).strip()
            if reason:
                # 비공개 사유는 초록 뒤에 덧붙여 보존 (심사·통계에 유용)
                base = candidate.abstract or ""
                candidate.abstract = (base + f" [원문 비공개 사유] {reason}")[:2000]
            return candidate

        urls: list[str] = []
        for f in data.get("fileList") or []:
            file_no = str(f.get("fileNo") or "").strip()
            if file_no:
                urls.append(f"{_BASE}/download/download.json?fileNo={file_no}")
        refr = str(dtl.get("refrUrl") or "").strip()
        if refr.startswith("http"):
            urls.append(refr)
        candidate.candidate_download_urls = urls[:5]
        return candidate

    def close(self) -> None:
        self._client.close()
