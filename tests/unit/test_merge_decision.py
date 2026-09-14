"""게시 단계 2차 중복 병합 판정 테스트 (외부 리뷰 3·4차).

precision 우선: 서로 다른 사건을 자동으로 합치는 것이
중복 두 건 남는 것보다 훨씬 나쁘다.
"""

from kiro_batch.publish import merge_decision


def test_same_event_very_high_similarity_merges():
    # 사실상 같은 제목 + 날짜 확인 → 병합
    assert merge_decision(0.9, shares_entity=False, date_diff_days=1)


def test_high_similarity_different_company_not_merged():
    # 'A기업, 휴머노이드 양산' vs 'B기업, 휴머노이드 양산' 패턴:
    # 유사도 0.85 미만 + 기관 불일치 → 병합 금지 (4차 보수화)
    assert not merge_decision(0.72, shares_entity=False, date_diff_days=1)


def test_very_high_similarity_unknown_date_needs_entity():
    # 날짜 미상이면 0.85 이상이라도 기관 일치 필수
    assert not merge_decision(0.9, shares_entity=False, date_diff_days=None)
    assert merge_decision(0.9, shares_entity=True, date_diff_days=None)


def test_medium_similarity_with_shared_entity_merges():
    assert merge_decision(0.5, shares_entity=True, date_diff_days=1)


def test_medium_similarity_without_entity_not_merged():
    assert not merge_decision(0.5, shares_entity=False, date_diff_days=0)


def test_low_similarity_never_merges():
    assert not merge_decision(0.44, shares_entity=True, date_diff_days=0)


def test_date_gap_blocks_merge():
    # 같은 제목이라도 발표일이 3일 넘게 차이나면 다른 사건으로 유지
    assert not merge_decision(0.95, shares_entity=True, date_diff_days=10)


def test_boundary_values():
    assert merge_decision(0.85, shares_entity=False, date_diff_days=3)
    assert not merge_decision(0.84, shares_entity=False, date_diff_days=3)
    assert merge_decision(0.45, shares_entity=True, date_diff_days=3)
    assert not merge_decision(0.85, shares_entity=False, date_diff_days=4)
