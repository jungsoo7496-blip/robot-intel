"""원문 HTML 수집과 본문 추출 (tasks §5.3).

- 타임아웃·최대 응답 크기 제한
- 외부 HTML은 저장 전에 본문 텍스트만 추출 (스크립트 미실행, NFR-004)
- 추출 실패 시 메타데이터를 보존한다 (실패 허용)
"""

from __future__ import annotations

import httpx
import trafilatura

from .net_guard import check_public_http_url

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; KIRO-RobotIntel/0.1; internal research bot)"
    ),
    "Accept-Language": "ko, en;q=0.8",
}


class FetchError(Exception):
    pass


def fetch_html(url: str, timeout: float = 20.0, max_bytes: int = 3_000_000) -> str:
    """URL에서 HTML을 가져온다. 실패·초과 시 FetchError.

    SSRF 방어: 요청 전과 리다이렉트 도착지 모두 공개 주소만 허용한다 (P1-8).
    """
    ok, reason = check_public_http_url(url)
    if not ok:
        raise FetchError(f"비공개 URL 차단: {reason}")
    try:
        with httpx.Client(
            headers=_HEADERS, timeout=timeout, follow_redirects=True
        ) as client:
            with client.stream("GET", url) as response:
                # 리다이렉트로 사설망에 도달하는 경로 차단
                final_ok, final_reason = check_public_http_url(str(response.url))
                if not final_ok:
                    raise FetchError(f"리다이렉트 차단: {final_reason}")
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise FetchError(f"응답 크기 초과({total} > {max_bytes})")
                    chunks.append(chunk)
        return b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
    except httpx.HTTPError as e:
        raise FetchError(f"HTTP 오류: {e}") from e


def extract_clean_text(html: str) -> str | None:
    """메뉴·광고·스크립트를 제외한 본문 텍스트. 실패 시 None."""
    if not html:
        return None
    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )
    if text:
        text = text.strip()
    return text or None
