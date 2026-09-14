"""국내 리전 경유 옵션 — 일부 국내 공공 사이트용 (2026-08-19).

우리 Next.js 앱이 서울 리전(vercel.json regions=["icn1"])에서 돌므로,
필요한 호스트에 한해 그 앱의 /api/relay 라우트를 경유해 접근한다.

동작 방식: httpx 클라이언트의 mounts에 이 트랜스포트를 지정 호스트에만
걸어서, 해당 호스트로 가는 요청을 중계 URL(?url=원래주소)로 바꿔 보낸다.
- REPORT_RELAY_URL·REPORT_RELAY_TOKEN 이 설정된 경우에만 활성화된다.
  로컬 개발에서는 env가 없으므로 기존처럼 직접 접속한다.
- Range 헤더는 그대로 전달한다 (access.py의 64KB 부분 다운로드 판정용).
- 중계 서버가 리다이렉트를 따라가 최종 응답을 돌려준다.
"""

from __future__ import annotations

import os

import httpx

# 국내 리전 경유가 필요한 호스트. 여기 없는 호스트는 직접 접속한다.
RELAYED_HOSTS = ("policy.nl.go.kr", "www.alio.go.kr", "alio.go.kr")


class RelayTransport(httpx.HTTPTransport):
    """지정 호스트로 가는 요청을 중계 라우트 경유로 재작성한다."""

    def __init__(self, relay_url: str, token: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._relay_url = relay_url
        self._token = token

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        relay_url = httpx.URL(self._relay_url).copy_add_param(
            "url", str(request.url)
        )
        headers = {"x-relay-token": self._token}
        if "range" in request.headers:
            headers["range"] = request.headers["range"]
        method = request.method if request.method in ("GET", "HEAD") else "GET"
        relay_request = httpx.Request(method, relay_url, headers=headers)
        response = super().handle_request(relay_request)
        # 호출자(Client)가 원래 요청과 응답을 짝지을 수 있게 되돌린다
        response.request = request
        return response


def relay_mounts() -> dict[str, httpx.BaseTransport]:
    """env가 설정된 경우 중계 대상 호스트의 mounts 사전을 돌려준다.

    httpx.Client(mounts=relay_mounts())로 쓴다. env 미설정이면 빈 사전 —
    클라이언트는 평소처럼 전부 직접 접속한다.
    """
    relay_url = os.environ.get("REPORT_RELAY_URL", "").strip()
    token = os.environ.get("REPORT_RELAY_TOKEN", "").strip()
    if not relay_url or not token:
        return {}
    transport = RelayTransport(relay_url, token)
    return {f"all://{host}": transport for host in RELAYED_HOSTS}
