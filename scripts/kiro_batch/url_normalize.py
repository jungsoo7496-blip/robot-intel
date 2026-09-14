"""URL 정규화 (설계 §8.2, tasks §5.4).

동일 페이지의 추적 URL·AMP·모바일 변형을 같은 canonical URL로 만든다.
canonical_url에는 UNIQUE 제약이 걸려 있어 중복 저장을 막는다 (FR-005).
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 명백한 추적·세션 파라미터만 제거한다. 콘텐츠 식별에 쓰일 수 있는
# 일반 파라미터(id, no, page 등)는 유지한다.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "dclid", "fbclid", "igshid", "msclkid", "twclid",
    "mc_cid", "mc_eid", "ref", "referrer", "cmpid", "cmp", "ncid",
    "phpsessid", "jsessionid", "sessionid", "session_id", "sid",
    "amp", "outputtype", "output_type",
}

_MOBILE_HOST_PREFIXES = ("m.", "mobile.", "amp.")


def normalize_url(url: str) -> str:
    """URL을 canonical 형태로 정규화한다."""
    url = url.strip()
    parts = urlsplit(url)

    # HTTP/HTTPS 정규화: 비교 목적의 canonical은 https로 통일
    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme

    host = parts.netloc.lower()
    # 기본 포트 제거
    if host.endswith(":80") or host.endswith(":443"):
        host = host.rsplit(":", 1)[0]
    # 모바일·AMP 서브도메인 정규화
    for prefix in _MOBILE_HOST_PREFIXES:
        if host.startswith(prefix):
            host = "www." + host[len(prefix):] if not host[len(prefix):].startswith("www.") else host[len(prefix):]
            break

    path = parts.path or "/"
    # AMP 경로 변형 제거
    for amp_seg in ("/amp/", "/amp"):
        if path.endswith(amp_seg):
            path = path[: -len(amp_seg)] or "/"
            break
    # 마지막 슬래시 정규화 (루트 제외)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    # 추적 파라미터 제거 + 정렬로 순서 차이 제거
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_pairs))

    # 프래그먼트 제거
    return urlunsplit((scheme, host, path, query, ""))


def url_hash(canonical_url: str) -> str:
    """정규화된 URL의 안정 해시(sha256 hex)."""
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
