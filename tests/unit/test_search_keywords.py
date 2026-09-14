"""검색 키워드 → 구글 RSS 주소·scope 필터 (DB·네트워크 없음).

핵심은 '지금 DB에 있는 구글 뉴스 4개 행의 URL을 검색어에서 그대로 다시 만들
수 있는가'다. 여기서 어긋나면 마이그레이션이 태깅한 관리행의 주소를 다음
배치가 다른 값으로 덮어써서, 수집 대상이 소리 없이 바뀐다.
"""

from __future__ import annotations

import pytest

from kiro_batch.search_keywords import (
    Keyword,
    NewsTarget,
    adapter_config_for,
    fetch_method_for,
    google_news_rss_url,
    google_query,
    keywords_for_use,
    naver_source_url,
    source_name,
)

# 2026-09-08 운영 DB에서 그대로 읽어 온 값 (sources.url)
LIVE_GOOGLE_URLS = {
    "로봇": (
        "https://news.google.com/rss/search?q=%EB%A1%9C%EB%B4%87"
        "&hl=ko&gl=KR&ceid=KR:ko"
    ),
    "로보틱스": (
        "https://news.google.com/rss/search?q=%EB%A1%9C%EB%B3%B4%ED%8B%B1%EC%8A%A4"
        "&hl=ko&gl=KR&ceid=KR:ko"
    ),
    "휴머노이드": (
        "https://news.google.com/rss/search?q=%ED%9C%B4%EB%A8%B8%EB%85%B8%EC%9D%B4%EB%93%9C"
        "&hl=ko&gl=KR&ceid=KR:ko"
    ),
}

LIVE_PHYSICAL_AI_URL = (
    "https://news.google.com/rss/search?q=%22%ED%94%BC%EC%A7%80%EC%BB%AC%20AI%22"
    "%20OR%20%22Physical%20AI%22%20OR%20%22Embodied%20AI%22"
    "&hl=ko&gl=KR&ceid=KR:ko"
)


@pytest.mark.parametrize("term,expected", sorted(LIVE_GOOGLE_URLS.items()))
def test_google_url_reproduces_live_rows(term: str, expected: str):
    assert google_news_rss_url(Keyword(term=term)) == expected


def test_google_url_reproduces_live_compound_row():
    """'피지컬 AI'만 복합 질의였다 — alt_terms가 그 비대칭을 흡수한다."""
    kw = Keyword(term="피지컬 AI", alt_terms=("Physical AI", "Embodied AI"))
    assert google_news_rss_url(kw) == LIVE_PHYSICAL_AI_URL


def test_google_query_without_alt_terms_is_bare_term():
    # 따옴표를 붙이면 현재 3개 행의 URL과 달라진다 — 붙이지 않는다
    assert google_query(Keyword(term="로봇")) == "로봇"


def test_google_query_joins_alt_terms_with_or():
    kw = Keyword(term="피지컬 AI", alt_terms=("Physical AI", "Embodied AI"))
    assert google_query(kw) == '"피지컬 AI" OR "Physical AI" OR "Embodied AI"'


def test_google_query_ignores_blank_alt_terms():
    kw = Keyword(term="로봇", alt_terms=("", "   "))
    assert google_query(kw) == "로봇"
    assert google_news_rss_url(kw) == LIVE_GOOGLE_URLS["로봇"]


def test_google_query_trims_alt_terms():
    kw = Keyword(term="로봇", alt_terms=("  robot  ",))
    assert google_query(kw) == '"로봇" OR "robot"'


def test_google_url_encodes_space_as_percent20_not_plus():
    """quote(safe='')이라야 공백이 %20이 된다 (quote_plus면 '+'가 돼 주소가 달라진다)."""
    url = google_news_rss_url(Keyword(term="로봇 정책"))
    assert "+" not in url.split("?q=", 1)[1].split("&", 1)[0]
    assert "%20" in url


def test_google_url_encodes_ampersand_so_query_is_not_split():
    url = google_news_rss_url(Keyword(term="A&B"))
    assert url == (
        "https://news.google.com/rss/search?q=A%26B&hl=ko&gl=KR&ceid=KR:ko"
    )


def test_naver_url_is_stable_identifier():
    assert naver_source_url(Keyword(term="로봇")) == "naver-news://query/%EB%A1%9C%EB%B4%87"
    # 검색어가 다르면 주소도 다르다 (부분 유니크 인덱스와 별개로 url UNIQUE)
    assert naver_source_url(Keyword(term="로봇")) != naver_source_url(
        Keyword(term="휴머노이드")
    )


def test_source_name_and_fetch_method_per_target():
    kw = Keyword(term="휴머노이드")
    assert source_name("naver", kw) == "네이버 뉴스 — 휴머노이드"
    assert source_name("google", kw) == "Google 뉴스 — 휴머노이드"
    assert fetch_method_for("naver") == "NAVER_API"
    assert fetch_method_for("google") == "RSS"


def test_adapter_config_naver_carries_query_and_display():
    target = NewsTarget(target_key="naver", name="네이버 뉴스 검색", display=200)
    # 네이버는 url이 아니라 adapter_config.query로 검색한다 (naver_news.py)
    assert adapter_config_for(target, Keyword(term="로봇")) == {
        "query": "로봇",
        "display": 200,
    }


def test_adapter_config_google_is_link_decoder_only():
    target = NewsTarget(target_key="google", name="구글 뉴스 RSS")
    # 구글은 검색어가 URL에 들어가므로 config에는 링크 해독기만 있으면 된다
    assert adapter_config_for(target, Keyword(term="로봇")) == {
        "link_decoder": "google_news"
    }


# --- scope 필터 (load_keywords의 순수 함수 부분) ---

ROWS = [
    {"term": "로봇", "alt_terms": [], "scope": "ALL", "sort_order": 10},
    {"term": "드론", "alt_terms": [], "scope": "REPORTS", "sort_order": 60},
    {"term": "로봇 정책", "alt_terms": [], "scope": "NEWS", "sort_order": 80},
    {
        "term": "피지컬 AI",
        "alt_terms": ["Physical AI"],
        "scope": "ALL",
        "sort_order": 40,
    },
]


def test_keywords_for_news_exclude_reports_only():
    terms = [k.term for k in keywords_for_use(ROWS, "news")]
    assert terms == ["로봇", "피지컬 AI", "로봇 정책"]  # sort_order 10·40·80


def test_keywords_for_reports_exclude_news_only():
    terms = [k.term for k in keywords_for_use(ROWS, "reports")]
    assert terms == ["로봇", "피지컬 AI", "드론"]  # sort_order 10·40·60


def test_keywords_for_use_rejects_unknown_use():
    with pytest.raises(ValueError):
        keywords_for_use(ROWS, "rnd")


def test_keywords_alt_terms_survive_row_conversion():
    kw = next(k for k in keywords_for_use(ROWS, "news") if k.term == "피지컬 AI")
    assert kw.alt_terms == ("Physical AI",)
    assert google_news_rss_url(kw).endswith("&hl=ko&gl=KR&ceid=KR:ko")
