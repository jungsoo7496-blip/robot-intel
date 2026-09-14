"""게시물 중복 자동 스윕 (사용자 지시 2026-08-09 — "매번 수동으로 할 순 없잖아").

게시 시점 병합(publish._merge_into_existing)은 "이미 공개된 기사"와만
비교하므로, 같은 배치·병행 실행에서 나란히 게시된 동일 사건 기사는
서로를 못 본다 (실사례: 백로그 병행 소진 때 롯데마트 제타 13건).
이 스윕이 매 분석 배치 끝에 공개 게시물끼리 사후 비교해 그 사각을 메운다.

- 판정은 파이프라인과 동일한 merge_decision(유사도+기관+발표일) — 규칙 단일화
- 그룹당 관련 출처 수 최다(동률이면 최초 게시) 1건만 유지, 나머지는
  클러스터 병합 표식 + 비공개 + CONTENT_HIDE 이력 (삭제 아님, 복구 가능)
- Gemini 호출 없음, 실행 ~1초대 — 배치 실패에 영향 주지 않게 best-effort

단독 실행: python -m kiro_batch.dedup_sweep
"""

from __future__ import annotations

from collections import defaultdict

import psycopg

from .publish import merge_decision

WINDOW_DAYS = 7
MAX_HIDES_PER_RUN = 50  # 폭주 방지 — 남으면 다음 배치가 이어서 정리


def _group_pairs(pairs: list[dict]) -> dict[str, list[str]]:
    """merge_decision을 통과한 쌍들을 union-find로 그룹화."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        a_ent = {e.lower() for e in (p["a_ent"] or [])}
        b_ent = {e.lower() for e in (p["b_ent"] or [])}
        shares = bool(a_ent & b_ent)
        diff = (
            abs((p["a_date"] - p["b_date"]).days)
            if p["a_date"] and p["b_date"]
            else None
        )
        if merge_decision(float(p["sim"]), shares, diff):
            parent[find(str(p["a_id"]))] = find(str(p["b_id"]))

    groups: dict[str, list[str]] = defaultdict(list)
    for node in list(parent):
        groups[find(node)].append(node)
    return {k: v for k, v in groups.items() if len(v) > 1}


def run_sweep(conn: psycopg.Connection) -> int:
    """공개 게시물 중복을 정리하고 숨긴 건수를 반환한다."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.id AS a_id, b.id AS b_id,
                   similarity(a.title, b.title) AS sim,
                   ca.entity_keys AS a_ent, cb.entity_keys AS b_ent,
                   ca.event_date AS a_date, cb.event_date AS b_date
            FROM published_items a
            JOIN published_items b ON a.id < b.id
              AND a.region = b.region
              AND similarity(a.title, b.title) >= 0.45
            JOIN content_clusters ca ON ca.id = a.cluster_id
            JOIN content_clusters cb ON cb.id = b.cluster_id
            WHERE a.is_visible AND b.is_visible
              AND a.published_at > now() - make_interval(days => %s)
              AND b.published_at > now() - make_interval(days => %s)
            """,
            (WINDOW_DAYS, WINDOW_DAYS),
        )
        dup_groups = _group_pairs(cur.fetchall())

        hidden = 0
        for members in dup_groups.values():
            if hidden >= MAX_HIDES_PER_RUN:
                break
            cur.execute(
                """
                SELECT id, cluster_id, title, related_source_count
                FROM published_items WHERE id = ANY(%s) AND is_visible
                ORDER BY related_source_count DESC NULLS LAST, published_at ASC
                """,
                (members,),
            )
            rows = cur.fetchall()
            if len(rows) < 2:
                continue
            keep, dups = rows[0], rows[1:]
            for d in dups:
                cur.execute(
                    "UPDATE cluster_members SET cluster_id = %s, "
                    "is_representative = false WHERE cluster_id = %s",
                    (keep["cluster_id"], d["cluster_id"]),
                )
                cur.execute(
                    "UPDATE content_clusters SET merged_into_cluster_id = %s, "
                    "merged_at = now() WHERE id = %s",
                    (keep["cluster_id"], d["cluster_id"]),
                )
                cur.execute(
                    "UPDATE published_items SET is_visible = false WHERE id = %s",
                    (d["id"],),
                )
                cur.execute(
                    """
                    INSERT INTO operation_events
                      (event_type, target_table, target_id, reason)
                    VALUES ('CONTENT_HIDE', 'published_items', %s,
                            '자동 중복 스윕 (배치 후 정리)')
                    """,
                    (d["id"],),
                )
                hidden += 1
            cur.execute(
                "UPDATE published_items SET related_source_count = %s WHERE id = %s",
                (sum(r["related_source_count"] or 1 for r in rows), keep["id"]),
            )
            cur.execute(
                "UPDATE content_clusters SET member_count = ("
                "  SELECT count(*) FROM cluster_members WHERE cluster_id = %s"
                ") WHERE id = %s",
                (keep["cluster_id"], keep["cluster_id"]),
            )
    conn.commit()
    return hidden


def main() -> int:
    from .config import Settings
    from .db import connect

    settings = Settings.from_env()
    settings.require("supabase_db_url")
    conn = connect(settings.supabase_db_url)
    try:
        hidden = run_sweep(conn)
        print(f"[dedup_sweep] 중복 {hidden}건 정리")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
