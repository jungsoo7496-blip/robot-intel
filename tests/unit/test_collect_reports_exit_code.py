"""보고서 수집 종료 코드 — 조합(채널×검색어) 하나가 실패해도 전체 실패가 아니다 (2026-09-22).

PRISM·ScienceON·NANET 같은 사이트 하나가 응답을 안 하면 그 조합만 FAILED로 기록되고
나머지는 정상 수집된다. 그런데 실패가 1개라도 있으면 프로세스가 1로 끝나 Actions 실행
전체가 '실패'로 찍혔다 (최근 5회 중 4회). 하나라도 성공했으면 0, 전부 실패했을 때만 1.
"""

from __future__ import annotations

import pytest

from kiro_batch.collect_reports import exit_code_for


@pytest.mark.parametrize(
    "succeeded, failed, expected",
    [
        (5, 0, 0),   # 전부 성공
        (4, 1, 0),   # 한 사이트 타임아웃 — 나머지는 수집됨
        (1, 9, 0),   # 하나라도 성공하면 정상 종료
        (0, 3, 1),   # 전부 실패 — 비밀값·네트워크 등 공통 원인 가능성
        (0, 0, 0),   # 실행할 조합이 없었음
    ],
)
def test_exit_code_for(succeeded, failed, expected):
    assert exit_code_for(succeeded, failed) == expected
