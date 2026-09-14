"""ScienceON(KISTI) 보고서 어댑터 (2026-08-17).

국가 R&D 보고서·KOSEN 동향 등 — API Gateway 경유.
인증 3요소: 인증키(SCIENCEON_AUTH_KEY, 채널 credential) + 클라이언트 ID
+ 신청 시 등록한 MAC 주소 (뒤 둘은 env에서 직접).

토큰 발급:
- {"datetime": YYYYMMDDHHMMSS, "mac_address": 등록값} 을 AES-256-CBC로
  암호화해 urlsafe base64로 보낸다 (IV는 _IV 상수).
- access token 2시간 유효 — 배치 한 번(수 분)에는 재발급 불필요.

검색: openapicall.do?action=search&target=REPORT&searchQuery={"BI":검색어,"PY":연도}
- 정렬 파라미터는 무시된다(관련도순 고정) → PY 연도를 하나씩 짚어 최신부터.
- PY는 단일 연도만 유효하다 (범위 지정은 0건).
- 목록 응답에 초록 전문·원문URL이 이미 있어 상세 호출이 필요 없다.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.parse
from datetime import date, datetime

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .base import ChannelConfig, ReportCandidate

_BASE = "https://apigateway.kisti.re.kr"
# 게이트웨이 호환을 위해 범용 UA를 쓴다.
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_IV = b"jvHJ1EFA0IXBrxxz"
_PAGE_SIZE = 100

_ITEM_RE = re.compile(
    r'<item metaCode="([^"]+)"[^>]*><!\[CDATA\[(.*?)\]\]></item>', re.DOTALL
)
_RECORD_RE = re.compile(r"<record\b.*?</record>", re.DOTALL)
_TOTAL_RE = re.compile(r"<TotalCount>(\d+)</TotalCount>")
_TAG_RE = re.compile(r"<[^>]+>")


def build_accounts(auth_key: str, mac_address: str, ts: str) -> str:
    """토큰 요청 accounts 파라미터 (ts 주입 — 테스트 가능한 순수 함수)."""
    plain = json.dumps(
        {"datetime": ts, "mac_address": mac_address}, separators=(",", ":")
    ).encode()
    pad = 16 - len(plain) % 16
    plain += bytes([pad]) * pad
    enc = Cipher(algorithms.AES(auth_key.encode()), modes.CBC(_IV)).encryptor()
    return base64.urlsafe_b64encode(enc.update(plain) + enc.finalize()).decode()


def _clean(raw: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", raw or "")).strip()


def _parse_record(block: str) -> dict[str, str]:
    return {code: value for code, value in _ITEM_RE.findall(block)}


class ScienceonAdapter:
    def __init__(self) -> None:
        # 게이트웨이가 연속 호출 중 keep-alive 연결을 일방적으로 끊는 일이
        # 있다 ("Server disconnected" — 첫 실전 실행에서 관측). 연결 수준
        # 오류는 transport 재시도로 흡수한다 (HTTP 오류 상태는 재시도 안 함).
        self._client = httpx.Client(
            headers=_HEADERS, timeout=30.0,
            transport=httpx.HTTPTransport(retries=2),
        )
        self._token: str | None = None
        self._client_id = os.environ.get("SCIENCEON_CLIENT_ID", "")
        self._mac = os.environ.get("SCIENCEON_MAC_ADDRESS", "")

    def _ensure_token(self, auth_key: str) -> str:
        if self._token:
            return self._token
        if not self._client_id or not self._mac:
            raise RuntimeError(
                "SCIENCEON_CLIENT_ID / SCIENCEON_MAC_ADDRESS env 미설정"
            )
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        accounts = urllib.parse.quote(build_accounts(auth_key, self._mac, ts))
        resp = self._client.get(
            f"{_BASE}/tokenrequest.do"
            f"?client_id={self._client_id}&accounts={accounts}"
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise RuntimeError(f"ScienceON 토큰 발급 실패: {resp.text[:200]}")
        self._token = token
        return token

    def fetch_recent(self, channel: ChannelConfig) -> list[ReportCandidate]:
        if not channel.credential:
            raise RuntimeError("SCIENCEON_AUTH_KEY 없음 (channel.credential)")
        token = self._ensure_token(channel.credential)

        year_from = int(channel.config.get("year_from", 2021))
        this_year = datetime.now().year
        out: list[ReportCandidate] = []

        for year in range(this_year, year_from - 1, -1):
            page = 1
            while page <= channel.max_pages:
                search_query = urllib.parse.quote(
                    json.dumps(
                        {"BI": channel.query or "로봇", "PY": str(year)},
                        ensure_ascii=False,
                    )
                )
                resp = self._client.get(
                    f"{_BASE}/openapicall.do?client_id={self._client_id}"
                    f"&token={token}&version=1.0&action=search&target=REPORT"
                    f"&searchQuery={search_query}"
                    f"&curPage={page}&rowCount={_PAGE_SIZE}"
                )
                resp.raise_for_status()
                text = resp.text
                records = _RECORD_RE.findall(text)
                for block in records:
                    fields = _parse_record(block)
                    cn = (fields.get("CN") or "").strip()
                    title = _clean(fields.get("Title") or "")
                    if not cn or not title:
                        continue
                    pubdate = re.sub(r"\D", "", fields.get("Pubdate") or "")
                    published = None
                    if len(pubdate) >= 8:
                        try:
                            published = date(
                                int(pubdate[:4]), int(pubdate[4:6]), int(pubdate[6:8])
                            )
                        except ValueError:
                            published = None
                    year_raw = (fields.get("Pubyear") or "").strip()
                    fulltext = (fields.get("FulltextURL") or "").strip()
                    detail = (fields.get("ContentURL") or "").strip() or fulltext
                    abstract = _clean(fields.get("Abstract") or "")
                    keywords = [
                        k.strip()
                        for k in re.split(r"[,;]", fields.get("Keyword") or "")
                        if k.strip()
                    ]
                    out.append(
                        ReportCandidate(
                            source_key=channel.source_key,
                            channel_key=channel.channel_key,
                            external_id=cn,
                            title=title,
                            detail_url=detail
                            or f"https://scienceon.kisti.re.kr/srch/selectPORSrchReport.do?cn={cn}",
                            institution=_clean(
                                fields.get("Publisher")
                                or fields.get("ManagingAgency") or ""
                            ) or None,
                            authors=[
                                a.strip()
                                for a in (fields.get("Author") or "").split(";")
                                if a.strip()
                            ][:5],
                            published_date=published,
                            published_year=int(year_raw)
                            if year_raw.isdigit() else year,
                            source_report_type=(fields.get("DBCode") or "").strip()
                            or None,
                            abstract=abstract[:2000] or None,
                            source_keywords=keywords[:10],
                            candidate_download_urls=[fulltext]
                            if fulltext.startswith("http") else [],
                            # 원문URL은 대개 뷰어/랜딩 페이지 — 직접 파일이 아니어도
                            # 공식 원문 접근 경로가 있다는 뜻이다
                            viewer_hint=bool(fulltext),
                        )
                    )
                    if len(out) >= channel.max_items:
                        return out
                total = int(_TOTAL_RE.search(text).group(1)) if _TOTAL_RE.search(text) else 0
                if page * _PAGE_SIZE >= total or not records:
                    break
                page += 1
                time.sleep(channel.request_interval_ms / 1000)
            # 연도 사이에도 쉰다 — 게이트웨이 연속 호출 부담 완화
            time.sleep(channel.request_interval_ms / 1000)
        return out

    def fetch_detail(self, candidate: ReportCandidate) -> ReportCandidate:
        """목록 응답에 초록·원문URL이 이미 포함 — 추가 호출 불필요."""
        return candidate

    def close(self) -> None:
        self._client.close()
