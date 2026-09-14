"""보고서 접근성 검증 (스펙 §10·11).

Gemini가 아니라 코드가 판정한다.
- HEAD 우선, 실패 시 Range GET(최대 64KB, 전체 다운로드 금지)
- `.pdf` URL이라는 이유만으로 / HTTP 200이라는 이유만으로 DOWNLOAD 판정 금지
- magic bytes(%PDF, HWP OLE/zip)와 Content-Type·Content-Disposition으로 확인
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; KIRO-RobotIntel/0.1)"}
_RANGE_BYTES = 64 * 1024

# 문서 파일 시그니처
_PDF_MAGIC = b"%PDF"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0"  # HWP 5.x / DOC / XLS
_ZIP_MAGIC = b"PK\x03\x04"        # HWPX / DOCX / XLSX / ZIP

_DOC_CONTENT_TYPES = (
    "application/pdf",
    "application/haansofthwp",
    "application/x-hwp",
    "application/vnd.hancom",
    "application/msword",
    "application/vnd.openxmlformats",
    "application/octet-stream",  # Disposition·magic으로 재확인
)


@dataclass
class AccessResult:
    status: str  # DIRECT_DOWNLOAD | VIEW_ONLY | METADATA_ONLY | BLOCKED | UNKNOWN
    http_status: int | None = None
    file_format: str | None = None
    file_name: str | None = None
    final_url: str | None = None
    note: str | None = None


def _filename_from_disposition(disposition: str | None) -> str | None:
    if not disposition:
        return None
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disposition)
    return m.group(1).strip() if m else None


def _format_from_magic(head: bytes) -> str | None:
    if head.startswith(_PDF_MAGIC):
        return "PDF"
    if head.startswith(_OLE_MAGIC):
        return "HWP/OLE"
    if head.startswith(_ZIP_MAGIC):
        return "HWPX/ZIP"
    return None


# 일부 정부 사이트(api.prism.go.kr 등)는 GPKI 중간 인증서를 체인에 안 실어
# certifi 검증이 실패한다. 공개 파일의 존재·형식 확인이 목적이므로 그 경우에
# 한해 검증 생략으로 1회 재시도하고 note에 남긴다.
_insecure_client: httpx.Client | None = None


def _get_insecure_client() -> httpx.Client:
    global _insecure_client
    if _insecure_client is None:
        _insecure_client = httpx.Client(headers=_HEADERS, verify=False)
    return _insecure_client


def check_download_url(client: httpx.Client, url: str) -> AccessResult:
    """단일 후보 URL이 실제 문서 파일인지 판정한다."""
    result = _check_download_url(client, url)
    if (
        result.status == "UNKNOWN"
        and result.note
        and "CERTIFICATE_VERIFY_FAILED" in result.note
        and ".go.kr" in url
    ):
        retry = _check_download_url(_get_insecure_client(), url)
        retry.note = ((retry.note or "") + " · TLS 체인 검증 생략(.go.kr GPKI)").strip(" ·")
        return retry
    return result


def _check_download_url(client: httpx.Client, url: str) -> AccessResult:
    # 1) HEAD 시도 — 403/405는 그 자체로 BLOCKED 판정 금지 (스펙 §11)
    head_status: int | None = None
    try:
        r = client.head(url, follow_redirects=True, timeout=10.0)
        head_status = r.status_code
        if r.status_code == 200:
            ctype = (r.headers.get("content-type") or "").lower()
            fname = _filename_from_disposition(r.headers.get("content-disposition"))
            if any(ctype.startswith(t) for t in _DOC_CONTENT_TYPES) and (
                not ctype.startswith("application/octet-stream") or fname
            ):
                # HEAD만으로는 HTML 오류페이지 위장을 못 걸러내므로 GET으로 확정
                pass
    except httpx.HTTPError:
        pass

    # 2) Range GET으로 확정 (본문 앞부분만 — 전체 다운로드 금지)
    try:
        with client.stream(
            "GET",
            url,
            headers={**_HEADERS, "Range": f"bytes=0-{_RANGE_BYTES - 1}"},
            follow_redirects=True,
            timeout=12.0,
        ) as r:
            status = r.status_code
            if status in (401, 403):
                return AccessResult("BLOCKED", status, note="인증/권한 필요")
            if status >= 400:
                return AccessResult("UNKNOWN", status, note=f"HTTP {status}")

            ctype = (r.headers.get("content-type") or "").lower()
            fname = _filename_from_disposition(r.headers.get("content-disposition"))
            head = b""
            for chunk in r.iter_bytes(chunk_size=8192):
                head += chunk
                if len(head) >= 16:
                    break
            fmt = _format_from_magic(head)

            if fmt:
                return AccessResult(
                    "DIRECT_DOWNLOAD", status, fmt, fname, str(r.url)
                )
            if ctype.startswith("text/html"):
                # HTML이 왔다 = 파일이 아니라 페이지 (오류/뷰어/로그인일 수 있음)
                return AccessResult(
                    "UNKNOWN", status, note="HTML 응답 — 직접 파일 아님"
                )
            return AccessResult("UNKNOWN", status, note=f"판정 불가 ({ctype[:40]})")
    except httpx.TimeoutException:
        return AccessResult("UNKNOWN", head_status, note="timeout")
    except httpx.HTTPError as e:
        return AccessResult("UNKNOWN", head_status, note=str(e)[:120])


def check_candidate(
    client: httpx.Client,
    download_urls: list[str],
    detail_url: str,
    has_viewer_hint: bool = False,
) -> AccessResult:
    """후보 URL들을 순서대로 검사해 최선의 접근 상태를 판정한다."""
    best: AccessResult | None = None
    for url in download_urls[:3]:  # 후보당 최대 3개 URL만 (부하 제한)
        result = check_download_url(client, url)
        if result.status == "DIRECT_DOWNLOAD":
            return result
        if best is None or result.status == "BLOCKED":
            best = result

    if has_viewer_hint:
        return AccessResult("VIEW_ONLY", note="공식 뷰어/원문 페이지 확인")
    if best and best.status == "BLOCKED":
        return best
    # 다운로드 후보가 없거나 판정 실패 — 상세페이지만 확보된 상태
    return AccessResult(
        "METADATA_ONLY" if not download_urls else (best.status if best else "UNKNOWN"),
        best.http_status if best else None,
        note=best.note if best else "다운로드 후보 없음",
    )
