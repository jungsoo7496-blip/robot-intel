"""공개 웹 URL 검증 — SSRF 방어 (외부 리뷰 P1-8).

배치는 GitHub Actions 러너에서 실행되므로 클라우드 메타데이터
엔드포인트(169.254.169.254 등)와 사설망 접근을 차단해야 한다.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

ALLOWED_PORTS = {80, 443}
BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localhost")


def check_public_http_url(url: str) -> tuple[bool, str]:
    """(허용 여부, 거부 사유). DNS를 조회해 실제 IP까지 검사한다."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False, "URL 형식 오류"

    if parts.scheme not in ("http", "https"):
        return False, "http(s)만 허용"
    if parts.username or parts.password:
        return False, "자격증명이 포함된 URL 금지"

    host = (parts.hostname or "").lower()
    if not host:
        return False, "호스트 없음"
    if host == "localhost" or host.endswith(BLOCKED_HOST_SUFFIXES):
        return False, f"내부 호스트명 금지: {host}"

    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        return False, f"허용되지 않은 포트: {port}"

    # IP 리터럴 또는 DNS 해석 결과가 공인 주소인지 확인
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False, "DNS 해석 실패"

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False, f"비공개 주소로 해석됨: {ip}"
    return True, ""
