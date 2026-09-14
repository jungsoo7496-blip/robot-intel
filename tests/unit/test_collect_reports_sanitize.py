"""collect_reports._safe_error — 오류 문구에 API 키가 남지 않는지 확인.

어댑터는 serviceKey 등을 GET 쿼리에 넣으므로 httpx 예외 문구에 키가 그대로
들어간다. 그 문구가 DB(report_source_runs.notes·last_error)와 운영 화면에
저장·표시되므로, 저장 직전 sanitize가 키를 지우는지 네트워크 없이 검증한다.
"""

from __future__ import annotations

import httpx
import pytest

from kiro_batch.collect_reports import _safe_error

SECRET = "SECRET-KEY-XYZ-0123456789"


def _status_error(status: int, url: str) -> httpx.HTTPStatusError:
    """MockTransport로 실제 httpx 예외를 만든다 (네트워크 접속 없음)."""
    transport = httpx.MockTransport(lambda request: httpx.Response(status))
    with httpx.Client(transport=transport) as client:
        resp = client.get(url)
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            resp.raise_for_status()
    return excinfo.value


def test_http_status_error_drops_query_with_key():
    e = _status_error(
        401,
        f"https://www.nkis.re.kr/nkisApi/search/ReportList.do"
        f"?serviceKey={SECRET}&otpHanNm=%EB%A1%9C%EB%B4%87&pageNo=1",
    )
    assert SECRET in str(e)  # 원본 예외에는 키가 들어 있다

    msg = _safe_error(e)
    assert SECRET not in msg
    assert "serviceKey" not in msg
    assert "401" in msg
    assert "nkisApi/search/ReportList.do" in msg  # 원인 추적용 경로는 남는다


def test_http_status_error_masks_scienceon_token_params():
    e = _status_error(
        403,
        f"https://apigateway.kisti.re.kr/tokenrequest.do"
        f"?accounts={SECRET}&client_id=kiro-client",
    )
    msg = _safe_error(e)
    assert SECRET not in msg
    assert "kiro-client" not in msg


def test_non_http_error_masks_credential_params():
    # HTTPStatusError가 아닌 예외(연결 실패 등)에도 URL이 섞여 들어올 수 있다
    e = RuntimeError(
        f"connect failed: https://example.go.kr/list?ServiceKey={SECRET}&pageSize=20"
    )
    msg = _safe_error(e)
    assert SECRET not in msg
    assert "ServiceKey=***" in msg
    assert "pageSize=20" in msg  # 키가 아닌 파라미터는 진단용으로 남긴다


def test_plain_error_message_is_unchanged():
    assert _safe_error(RuntimeError("어댑터 미구현: 'foo'")) == "어댑터 미구현: 'foo'"
