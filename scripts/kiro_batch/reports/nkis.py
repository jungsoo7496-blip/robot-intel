"""NKIS(국가정책연구포털) 어댑터 — 공식 OpenAPI (스펙 §12·13).

국무조정실 산하 26개 국책연구기관의 연구보고서. 사용자 발급 키(2026-08-19).
- 목록: https://nkis.re.kr/nkisApi/search/ReportList.do
    serviceKey(필수)·otpHanNm(제목 포함 검색)·pageNo·rowCnt·pblYyBegin/End
- 상세: https://nkis.re.kr/nkisApi/search/ReportDetail.do
    serviceKey·otpId·otpSeq → HAN_ABS(초록 전문)·OTC_NM_STR(보고서 유형)

응답은 XML(목록은 EUC-KR, 상세는 UTF-8 + 잡음 DOCTYPE 행이 섞임) —
ElementTree가 DOCTYPE에서 넘어지므로 정규식으로 필드를 뽑는다.

원문 PDF: 상세 API에는 없고, ORG_LINK(subject_view1.do) HTML의
citation_pdf_url 메타 태그가 로그인 없는 미리보기 PDF를 가리킨다 (실측).
다운로드 버튼 자체는 로그인 필수라 쓰지 않는다.
"""

from __future__ import annotations

import html as html_mod
import re
import time
from datetime import date

import httpx

from .base import ChannelConfig, ReportCandidate

_LIST_URL = "https://nkis.re.kr/nkisApi/search/ReportList.do"
_DETAIL_URL = "https://nkis.re.kr/nkisApi/search/ReportDetail.do"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; KIRO-RobotIntel/0.1)"}
_PAGE_SIZE = 50

_RESULT_RE = re.compile(r"<result>(.*?)</result>", re.DOTALL)
_PDF_META_RE = re.compile(
    r'citation_pdf_url"\s+content="([^"]+)"'
)
_PREVIEWER_RE = re.compile(r"previewer\(")


def _field(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.DOTALL)
    return html_mod.unescape(m.group(1)).strip() if m else ""


def _parse_year(raw: str) -> int | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 4:
        year = int(digits[:4])
        if 1900 <= year <= 2100:
            return year
    return None


class NkisAdapter:
    def __init__(self) -> None:
        self._client = httpx.Client(headers=_HEADERS, timeout=20.0)
        self._credential: str | None = None

    def fetch_recent(self, channel: ChannelConfig) -> list[ReportCandidate]:
        if not channel.credential:
            raise RuntimeError("NKIS_API_KEY 없음 (channel.credential)")
        self._credential = channel.credential

        out: list[ReportCandidate] = []
        for page in range(1, channel.max_pages + 1):
            params: dict[str, object] = {
                "serviceKey": channel.credential,
                "otpHanNm": channel.query or "로봇",
                "pageNo": page,
                "rowCnt": _PAGE_SIZE,
            }
            year_from = channel.config.get("year_from")
            if year_from:
                params["pblYyBegin"] = int(year_from)
            resp = self._client.get(_LIST_URL, params=params)
            resp.raise_for_status()
            blocks = _RESULT_RE.findall(resp.text)
            for block in blocks:
                otp_id = _field(block, "OTP_ID")
                otp_seq = _field(block, "OTP_SEQ") or "0"
                title = re.sub(r"\s+", " ", _field(block, "OTP_HAN_NM"))
                if not otp_id or not title:
                    continue
                author = _field(block, "INCHARGE_NM")
                keywords = [
                    k for k in (
                        _field(block, "LCLA_SCS_NM"), _field(block, "MCLA_SCS_NM")
                    ) if k
                ]
                out.append(
                    ReportCandidate(
                        source_key=channel.source_key,
                        channel_key=channel.channel_key,
                        external_id=f"{otp_id}|{otp_seq}",
                        title=title,
                        detail_url=(
                            "https://www.nkis.re.kr/subject_view1.do"
                            f"?otpId={otp_id}&otpSeq={otp_seq}"
                        ),
                        institution=_field(block, "PUBAGC") or None,
                        authors=[author] if author else [],
                        published_year=_parse_year(_field(block, "PBL_YY")),
                        source_keywords=keywords,
                    )
                )
                if len(out) >= channel.max_items:
                    return out
            if len(blocks) < _PAGE_SIZE:
                break
            time.sleep(channel.request_interval_ms / 1000)
        return out

    def fetch_detail(self, candidate: ReportCandidate) -> ReportCandidate:
        otp_id, _, otp_seq = candidate.external_id.partition("|")
        # 1) 상세 API — 초록 전문(HAN_ABS)과 보고서 유형
        try:
            resp = self._client.get(
                _DETAIL_URL,
                params={
                    "serviceKey": self._credential or "",
                    "otpId": otp_id,
                    "otpSeq": otp_seq or "0",
                },
            )
            resp.raise_for_status()
            abstract = _field(resp.text, "HAN_ABS")
            if abstract:
                candidate.abstract = re.sub(r"\s+", " ", abstract)[:2000]
            report_type = _field(resp.text, "OTC_NM_STR")
            if report_type:
                candidate.source_report_type = report_type
        except httpx.HTTPError:
            pass

        # 2) 포털 상세 HTML — citation_pdf_url(로그인 불필요 미리보기 PDF)
        try:
            page = self._client.get(candidate.detail_url, follow_redirects=True)
            if page.status_code == 200:
                m = _PDF_META_RE.search(page.text)
                if m:
                    candidate.candidate_download_urls = [
                        html_mod.unescape(m.group(1))
                    ]
                candidate.viewer_hint = bool(_PREVIEWER_RE.search(page.text))
        except httpx.HTTPError:
            pass
        return candidate

    def close(self) -> None:
        self._client.close()
