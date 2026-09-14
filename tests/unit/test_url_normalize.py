"""URL 정규화 단위 테스트 (tasks §18.1)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from kiro_batch.url_normalize import normalize_url, url_hash


def test_utm_params_removed():
    url = "https://example.com/news/123?utm_source=x&utm_medium=social&id=5"
    assert normalize_url(url) == "https://example.com/news/123?id=5"


def test_same_page_tracking_variants_merge():
    a = "https://example.com/article?utm_campaign=a&fbclid=xyz"
    b = "http://example.com/article/"
    assert normalize_url(a) == normalize_url(b)


def test_fragment_removed():
    assert normalize_url("https://example.com/a#section-2") == "https://example.com/a"


def test_trailing_slash_normalized():
    assert normalize_url("https://example.com/news/") == "https://example.com/news"
    # 루트 경로는 유지
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_http_https_merge():
    assert normalize_url("http://example.com/a") == normalize_url("https://example.com/a")


def test_amp_path_normalized():
    assert normalize_url("https://example.com/news/123/amp") == \
        normalize_url("https://example.com/news/123")


def test_mobile_subdomain_normalized():
    assert normalize_url("https://m.example.com/news/1") == \
        normalize_url("https://www.example.com/news/1")


def test_query_order_stable():
    a = normalize_url("https://example.com/a?b=2&a=1")
    b = normalize_url("https://example.com/a?a=1&b=2")
    assert a == b


def test_meaningful_params_kept():
    # 콘텐츠 식별 파라미터는 남긴다 (다른 페이지를 잘못 합치지 않기, tasks §5.4)
    a = normalize_url("https://example.com/view?articleNo=100")
    b = normalize_url("https://example.com/view?articleNo=200")
    assert a != b


def test_url_hash_stable():
    u = normalize_url("https://example.com/a")
    assert url_hash(u) == url_hash(u)
    assert len(url_hash(u)) == 64
