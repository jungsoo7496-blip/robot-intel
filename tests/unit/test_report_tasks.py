"""보고서 실행 단위 = (채널 × 검색어) 조합 — ChannelTask 계약 (DB 없음).

fetch_due_tasks는 SQL이라 단위 테스트 대상이 아니다(적용 후 통합 확인).
여기서는 어댑터에 넘어가는 query 값이 정확한지만 고정한다 — prism처럼
검색어를 쓰지 않는 채널이 실수로 빈 문자열로 검색하면 결과가 통째로 달라진다.
"""

from __future__ import annotations

from kiro_batch.reports.base import ChannelConfig
from kiro_batch.reports.repository import ChannelTask


def _task(keyword_term: str, **kw) -> ChannelTask:
    base = dict(
        source_id="11111111-1111-1111-1111-111111111111",
        source_key="nanet",
        source_priority=50,
        channel_id="22222222-2222-2222-2222-222222222222",
        channel_key="web",
        adapter_key="nanet",
        keyword_term=keyword_term,
        config={"dbname": "웹자료", "year_from": 2021},
        max_pages=10,
        max_items=200,
        request_interval_ms=1000,
        credential_key_name="DATA_GO_KR_API_KEY",
    )
    base.update(kw)
    return ChannelTask(**base)


def test_keyword_task_query_is_the_term():
    assert _task("휴머노이드").query == "휴머노이드"


def test_empty_keyword_means_no_query():
    """'' = 검색어 미사용 채널(prism). None이어야 어댑터가 전체 조회로 간다."""
    assert _task("").query is None


def test_whitespace_keyword_is_kept_as_is():
    # 공백만 있는 검색어는 화면에서 막는다 — 배치는 값을 임의로 바꾸지 않는다
    assert _task(" ").query == " "


def test_task_feeds_channel_config_unchanged():
    """run_channel이 만드는 ChannelConfig — query 말고는 종전과 같다."""
    task = _task("드론")
    config = ChannelConfig(
        source_key=task.source_key,
        channel_key=task.channel_key,
        query=task.query,
        config=task.config,
        max_pages=task.max_pages,
        max_items=task.max_items,
        request_interval_ms=task.request_interval_ms,
        credential=None,
    )
    assert config.query == "드론"
    assert config.config["dbname"] == "웹자료"  # 하위 갈래 구분은 보존된다
    assert config.max_pages == 10 and config.max_items == 200


def test_prism_style_task_reaches_adapter_default_free_of_keyword():
    """어댑터는 `channel.query or "로봇"` 관용구를 쓴다 — prism은 query를 안 본다."""
    task = _task("", source_key="prism", channel_key="research-window",
                 adapter_key="prism", config={"years_back": 4})
    assert task.query is None
    # 검색어를 쓰는 채널과 달리 조합이 늘어도 prism 작업 수는 1이다
    assert task.keyword_term == ""


def test_same_channel_different_keywords_are_distinct_tasks():
    a, b = _task("로봇"), _task("휴머노이드")
    assert a.channel_id == b.channel_id
    assert (a.channel_id, a.keyword_term) != (b.channel_id, b.keyword_term)
