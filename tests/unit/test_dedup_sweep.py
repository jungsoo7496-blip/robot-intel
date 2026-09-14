"""자동 중복 스윕 — 그룹화 순수 로직 테스트."""

from datetime import date

from kiro_batch.dedup_sweep import _group_pairs


def pair(a, b, sim, a_ent=None, b_ent=None, d1=None, d2=None):
    return {
        "a_id": a, "b_id": b, "sim": sim,
        "a_ent": a_ent or [], "b_ent": b_ent or [],
        "a_date": d1, "b_date": d2,
    }


def test_high_similarity_with_dates_groups():
    # 0.85+ & 발표일 확인 → 기관 불일치여도 병합 (merge_decision 규칙)
    groups = _group_pairs(
        [pair("a", "b", 0.9, d1=date(2026, 8, 9), d2=date(2026, 8, 9))]
    )
    assert len(groups) == 1 and sorted(next(iter(groups.values()))) == ["a", "b"]


def test_mid_similarity_needs_shared_entity():
    assert _group_pairs([pair("a", "b", 0.6)]) == {}
    groups = _group_pairs(
        [pair("a", "b", 0.6, a_ent=["롯데마트"], b_ent=["롯데마트"])]
    )
    assert len(groups) == 1


def test_chain_groups_transitively():
    # a~b, b~c → {a,b,c} 한 그룹 (13건짜리 다발도 하나로)
    e = ["제타"]
    groups = _group_pairs(
        [
            pair("a", "b", 0.6, a_ent=e, b_ent=e),
            pair("b", "c", 0.6, a_ent=e, b_ent=e),
        ]
    )
    assert len(groups) == 1
    assert sorted(next(iter(groups.values()))) == ["a", "b", "c"]


def test_date_gap_blocks_merge():
    groups = _group_pairs(
        [pair("a", "b", 0.9, d1=date(2026, 8, 1), d2=date(2026, 8, 9))]
    )
    assert groups == {}
