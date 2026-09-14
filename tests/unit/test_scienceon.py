"""ScienceON 어댑터 테스트 (2026-08-17) — 토큰 암호화·XML 파싱."""

import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from kiro_batch.reports.base import ChannelConfig
from kiro_batch.reports.scienceon import (
    ScienceonAdapter,
    _IV,
    build_accounts,
)

_KEY = "0123456789abcdef0123456789abcdef"  # 32자 테스트 키


def test_build_accounts_roundtrip():
    """암호문을 같은 키·고정 IV로 풀면 원래 JSON이 나온다 (urlsafe base64)."""
    accounts = build_accounts(_KEY, "00-00-00-00-00-00", "20260817120000")
    cipher_bytes = base64.urlsafe_b64decode(accounts)
    dec = Cipher(algorithms.AES(_KEY.encode()), modes.CBC(_IV)).decryptor()
    plain = dec.update(cipher_bytes) + dec.finalize()
    plain = plain[: -plain[-1]]  # PKCS7 언패딩
    data = json.loads(plain)
    assert data == {
        "datetime": "20260817120000",
        "mac_address": "00-00-00-00-00-00",
    }


_TOKEN_JSON = {"access_token": "tok123", "refresh_token": "r", "client_id": "cid"}

_SEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<MetaData>
 <resultSummary><statusCode>200</statusCode><TotalCount>1</TotalCount></resultSummary>
 <recordList>
  <record rownum="1">
   <item metaCode="CN" metaName="CN"><![CDATA[TRKO202400012345]]></item>
   <item metaCode="DBCode" metaName="DB코드"><![CDATA[TRKO]]></item>
   <item metaCode="Publisher" metaName="발행기관"><![CDATA[한국로봇융합연구원]]></item>
   <item metaCode="Pubdate" metaName="발행년월(일)"><![CDATA[20240315]]></item>
   <item metaCode="Pubyear" metaName="발행년"><![CDATA[2024]]></item>
   <item metaCode="Title" metaName="보고서제목"><![CDATA[수중  로봇 <b>실증</b> 연구]]></item>
   <item metaCode="Abstract" metaName="초록"><![CDATA[<font color='blue'>수중로봇</font> 실증  초록이다.]]></item>
   <item metaCode="Author" metaName="저자"><![CDATA[홍길동;김철수]]></item>
   <item metaCode="FulltextURL" metaName="원문URL"><![CDATA[https://scienceon.kisti.re.kr/srch/full/12345]]></item>
   <item metaCode="ContentURL" metaName="상세링크"><![CDATA[http://click.ndsl.kr/servlet/OpenAPIDetailView?cn=TRKO202400012345]]></item>
   <item metaCode="Keyword" metaName="주제어"><![CDATA[수중로봇, 실증]]></item>
  </record>
 </recordList>
</MetaData>
"""


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("tokenrequest.do"):
        return httpx.Response(200, json=_TOKEN_JSON)
    if request.url.path.endswith("openapicall.do"):
        assert "token=tok123" in str(request.url)
        sq = json.loads(request.url.params["searchQuery"])
        assert sq["BI"] == "로봇"
        assert sq["PY"].isdigit()  # 단일 연도 (범위는 0건 — 실측)
        # 최신 연도만 결과 반환, 과거 연도는 빈 목록
        if sq["PY"] == "2024":
            return httpx.Response(200, text=_SEARCH_XML)
        return httpx.Response(
            200,
            text="<MetaData><resultSummary><statusCode>200</statusCode>"
                 "<TotalCount>0</TotalCount></resultSummary>"
                 "<recordList></recordList></MetaData>",
        )
    return httpx.Response(404)


def _make_adapter(monkeypatch) -> ScienceonAdapter:
    monkeypatch.setenv("SCIENCEON_CLIENT_ID", "cid")
    monkeypatch.setenv("SCIENCEON_MAC_ADDRESS", "00-00-00-00-00-00")
    adapter = ScienceonAdapter()
    adapter._client = httpx.Client(transport=httpx.MockTransport(_handler))
    adapter._client_id = "cid"
    adapter._mac = "00-00-00-00-00-00"
    return adapter


def test_scienceon_fetch_recent(monkeypatch):
    adapter = _make_adapter(monkeypatch)
    channel = ChannelConfig(
        source_key="scienceon", channel_key="report-robot", query="로봇",
        config={"year_from": 2023}, max_pages=2, max_items=50,
        request_interval_ms=0, credential=_KEY,
    )
    out = adapter.fetch_recent(channel)
    assert len(out) == 1
    c = out[0]
    assert c.external_id == "TRKO202400012345"
    assert c.title == "수중 로봇 실증 연구"          # 태그·공백 정리
    assert c.abstract == "수중로봇 실증 초록이다."   # font 태그 제거
    assert c.institution == "한국로봇융합연구원"
    assert c.published_date is not None and c.published_date.isoformat() == "2024-03-15"
    assert c.authors == ["홍길동", "김철수"]
    assert c.source_keywords == ["수중로봇", "실증"]
    assert c.candidate_download_urls == [
        "https://scienceon.kisti.re.kr/srch/full/12345"
    ]
    assert c.viewer_hint is True
    assert c.source_report_type == "TRKO"


def test_scienceon_requires_credential(monkeypatch):
    adapter = _make_adapter(monkeypatch)
    channel = ChannelConfig(
        source_key="scienceon", channel_key="t", query="로봇", config={},
        max_pages=1, max_items=10, request_interval_ms=0, credential=None,
    )
    with pytest.raises(RuntimeError):
        adapter.fetch_recent(channel)
