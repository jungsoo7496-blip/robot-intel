"""NKIS·ALIO 어댑터 + 서울 중계 단위 테스트 (2026-08-19).

네트워크 없이 httpx.MockTransport로 실측 응답 형태를 재현한다.
(NKIS 응답의 잡음 DOCTYPE, ALIO의 비공개 문서 등 — 전부 실제로 관측된 형태)
"""

import json

import httpx
import pytest

from kiro_batch.reports.alio import AlioAdapter
from kiro_batch.reports.base import ChannelConfig
from kiro_batch.reports.nkis import NkisAdapter
from kiro_batch.reports.relay import RelayTransport, relay_mounts


def _channel(source_key, query, config=None, credential=None):
    return ChannelConfig(
        source_key=source_key,
        channel_key="test",
        query=query,
        config=config or {},
        max_pages=2,
        max_items=50,
        request_interval_ms=0,
        credential=credential,
    )


# ── NKIS ────────────────────────────────────────────────────────────

_NKIS_LIST_XML = """
<root>
  <TOTAL_COUNT>2</TOTAL_COUNT>
  <results>
    <result>
      <OTP_ID>OTP_0000000000016829</OTP_ID>
      <OTP_SEQ>0</OTP_SEQ>
      <LCLA_SCS_NM>일반공공행정 및 공공안전</LCLA_SCS_NM>
      <MCLA_SCS_NM>법제도</MCLA_SCS_NM>
      <OTP_HAN_NM>로봇산업 관련  규제혁신 연구</OTP_HAN_NM>
      <INCHARGE_NM>현대호</INCHARGE_NM>
      <PUBAGC>한국법제연구원</PUBAGC>
      <PBL_YY>2025  </PBL_YY>
      <ORG_LINK>https://www.nkis.re.kr/subject_view1.do?otpId=OTP_0000000000016829&amp;otpSeq=0</ORG_LINK>
    </result>
    <result>
      <OTP_ID></OTP_ID>
      <OTP_HAN_NM>ID 없는 행 — 건너뛰어야 함</OTP_HAN_NM>
    </result>
  </results>
</root>
"""

# 실측: 상세 응답 앞에 DOCTYPE html 잡음이 붙는다
_NKIS_DETAIL_XML = """
<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN" "http://www.w3.org/TR/html4/loose.dtd">
<root>
  <result>
    <OTC_NM_STR>일반연구보고서</OTC_NM_STR>
    <HAN_ABS>로봇산업   규제혁신을 위한
    연구 초록이다.</HAN_ABS>
  </result>
</root>
"""

_NKIS_PAGE_HTML = """
<html><head>
<meta name="citation_pdf_url" content="https://www.nkis.re.kr/preview.do?orgnFilePathNm=COM_0000000000023933" />
</head><body>
<a onclick="javascript:previewer('OTP_0000000000016829', 'COM_0000000000023933');">원문보기</a>
</body></html>
"""


def _nkis_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("ReportList.do"):
        assert request.url.params["serviceKey"] == "TESTKEY"
        assert request.url.params["otpHanNm"] == "로봇"
        assert request.url.params["pblYyBegin"] == "2021"
        return httpx.Response(200, text=_NKIS_LIST_XML)
    if path.endswith("ReportDetail.do"):
        return httpx.Response(200, text=_NKIS_DETAIL_XML)
    if path.endswith("subject_view1.do"):
        return httpx.Response(200, text=_NKIS_PAGE_HTML)
    return httpx.Response(404)


def test_nkis_fetch_recent_parses_xml():
    adapter = NkisAdapter()
    adapter._client = httpx.Client(transport=httpx.MockTransport(_nkis_handler))
    out = adapter.fetch_recent(
        _channel("nkis", "로봇", {"year_from": 2021}, credential="TESTKEY")
    )
    assert len(out) == 1  # ID 없는 행은 건너뜀
    c = out[0]
    assert c.external_id == "OTP_0000000000016829|0"
    assert c.title == "로봇산업 관련 규제혁신 연구"  # 공백 정규화
    assert c.institution == "한국법제연구원"
    assert c.published_year == 2025
    assert c.authors == ["현대호"]
    assert "otpId=OTP_0000000000016829" in c.detail_url
    assert c.source_keywords == ["일반공공행정 및 공공안전", "법제도"]


def test_nkis_fetch_detail_gets_abstract_and_pdf():
    adapter = NkisAdapter()
    adapter._client = httpx.Client(transport=httpx.MockTransport(_nkis_handler))
    adapter._credential = "TESTKEY"
    cand = adapter.fetch_recent(
        _channel("nkis", "로봇", {"year_from": 2021}, credential="TESTKEY")
    )[0]
    cand = adapter.fetch_detail(cand)
    assert cand.abstract.startswith("로봇산업 규제혁신")  # DOCTYPE 잡음 무관
    assert cand.source_report_type == "일반연구보고서"
    assert cand.candidate_download_urls == [
        "https://www.nkis.re.kr/preview.do?orgnFilePathNm=COM_0000000000023933"
    ]
    assert cand.viewer_hint is True


def test_nkis_requires_credential():
    adapter = NkisAdapter()
    with pytest.raises(RuntimeError):
        adapter.fetch_recent(_channel("nkis", "로봇"))


# ── ALIO ────────────────────────────────────────────────────────────

_ALIO_LIST = {
    "status": "success",
    "data": {
        "totalCnt": 2,
        "page": {"totalPage": 1},
        "result": [
            {
                "seq": "3510329",
                "rtitle": "주행 이동로봇과 비부착 센싱 기반  장대교량 건전성평가 연구",
                "pname": "한국도로공사",
                "author": "홍길동",
                "publishDt": "2026.05.02",
            },
            {
                "seq": "3564282",
                "rtitle": "전력 ICT 센터 로봇 적용 기준 수립 및 실증 최종보고서",
                "pname": "한국전력공사",
                "author": "이재경",
                "publishDt": "2026.08.07",
            },
        ],
    },
}

_ALIO_DTL_PUBLIC = {
    "data": {
        "researchDtl": {
            "content": "제1장 연구의 개요\n\n제2장 연구 수행 내용",
            "originalPrivateNm": "공개",
            "refrUrl": "https://keri.koreaexim.go.kr/HPHFOE052M01/112167",
        },
        "fileList": [{"fileNm": "6-4.pdf", "fileNo": "3039688"}],
    }
}

_ALIO_DTL_PRIVATE = {
    "data": {
        "researchDtl": {
            "content": "사족보행로봇 기반 자율 순시·점검 연구",
            "originalPrivateNm": "비공개",
            "originalPrivateContent": "지식재산권 확보에 영향을 미칠 수 있는 기술정보",
            "refrUrl": None,
        },
        "fileList": [],
    }
}


def _alio_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("findResearchList.json"):
        assert request.url.params["type"] == "title"
        assert request.url.params["word"] == "로봇"
        return httpx.Response(200, json=_ALIO_LIST)
    if path.endswith("findResearchDtl.json"):
        seq = request.url.params["seq"]
        return httpx.Response(
            200, json=_ALIO_DTL_PUBLIC if seq == "3510329" else _ALIO_DTL_PRIVATE
        )
    return httpx.Response(404)


def test_alio_fetch_recent_parses_json():
    adapter = AlioAdapter()
    adapter._client = httpx.Client(transport=httpx.MockTransport(_alio_handler))
    out = adapter.fetch_recent(_channel("alio", "로봇"))
    assert len(out) == 2
    c = out[0]
    assert c.external_id == "3510329"
    assert c.title == "주행 이동로봇과 비부착 센싱 기반 장대교량 건전성평가 연구"
    assert c.institution == "한국도로공사"
    assert c.published_date is not None and c.published_date.year == 2026
    assert c.detail_url.endswith("researchDtl.do?seq=3510329")


def test_alio_public_document_gets_files_and_refr_url():
    adapter = AlioAdapter()
    adapter._client = httpx.Client(transport=httpx.MockTransport(_alio_handler))
    cand = adapter.fetch_recent(_channel("alio", "로봇"))[0]
    cand = adapter.fetch_detail(cand)
    assert cand.abstract.startswith("제1장 연구의 개요")
    assert (
        "https://www.alio.go.kr/download/download.json?fileNo=3039688"
        in cand.candidate_download_urls
    )
    assert any("koreaexim" in u for u in cand.candidate_download_urls)


def test_alio_private_document_has_no_download_urls():
    adapter = AlioAdapter()
    adapter._client = httpx.Client(transport=httpx.MockTransport(_alio_handler))
    cand = adapter.fetch_recent(_channel("alio", "로봇"))[1]
    cand = adapter.fetch_detail(cand)
    assert cand.candidate_download_urls == []
    assert "[원문 비공개 사유]" in cand.abstract  # 사유 보존


# ── 서울 중계 ─────────────────────────────────────────────────────────

def test_relay_mounts_empty_without_env(monkeypatch):
    monkeypatch.delenv("REPORT_RELAY_URL", raising=False)
    monkeypatch.delenv("REPORT_RELAY_TOKEN", raising=False)
    assert relay_mounts() == {}


def test_relay_mounts_cover_blocked_hosts(monkeypatch):
    monkeypatch.setenv("REPORT_RELAY_URL", "https://robotreport.vercel.app/api/relay")
    monkeypatch.setenv("REPORT_RELAY_TOKEN", "tok")
    mounts = relay_mounts()
    assert "all://policy.nl.go.kr" in mounts
    assert "all://www.alio.go.kr" in mounts


def test_relay_transport_rewrites_request(monkeypatch):
    captured = {}

    def fake_handle(self, request):  # noqa: ANN001
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        captured["token"] = request.headers.get("x-relay-token")
        captured["range"] = request.headers.get("range")
        return httpx.Response(200, text="ok", request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", fake_handle)
    transport = RelayTransport("https://robotreport.vercel.app/api/relay", "tok")
    original = httpx.Request(
        "GET",
        "https://policy.nl.go.kr/openapi/searchKwdApi.do?kwd=로봇",
        headers={"Range": "bytes=0-65535"},
    )
    response = transport.handle_request(original)
    assert response.status_code == 200
    assert captured["url"].startswith("https://robotreport.vercel.app/api/relay")
    assert captured["params"]["url"].startswith(
        "https://policy.nl.go.kr/openapi/searchKwdApi.do"
    )
    assert captured["token"] == "tok"
    assert captured["range"] == "bytes=0-65535"
    # 응답이 원래 요청과 짝지어졌는지 (Client의 리다이렉트·이력 처리용)
    assert response.request is original
