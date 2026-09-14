"""단순 중복 클러스터링 (FR-005, 설계 §8.4, tasks §6.3).

URL 동일은 canonical_url UNIQUE가 이미 처리한다. 여기서는
제목 유사도 + 발표일 3일 이내 + 기관·기업·정책명 일치로 같은 사건을 묶는다.
"""

from __future__ import annotations

import re
from datetime import date, datetime

import psycopg

from .similarity import is_same_event

# 대표 출처 우선순위 (설계 §8.4): 낮을수록 우선
REPRESENTATIVE_RANK = {
    "정부·공공기관": 0,
    "정책·사업 공고": 0,
    "기업 공식 발표": 1,
    "연구기관·대학": 1,
    "전문 기술 큐레이션 매체": 2,
    "로봇·산업 전문매체": 2,
    "일반 언론": 3,
}

# 기관·기업·정책명 후보 추출용 간단 휴리스틱 (Phase 1)
_KOREAN_ORG_RE = re.compile(
    r"[가-힣A-Za-z0-9·]{2,20}(?:부|처|청|원|공사|공단|진흥원|연구원|연구소|협회|대학교|대학)"
)
_ENGLISH_NAME_RE = re.compile(r"\b[A-Z][A-Za-z0-9&\-]{2,}\b")
_PROJECT_RE = re.compile(r"[「『'\"]([^」』'\"]{2,40})[」』'\"]")


def extract_entity_keys(title: str, body: str | None = None) -> list[str]:
    """제목(과 본문 앞부분)에서 기관·기업·정책명 후보를 뽑는다."""
    text = f"{title or ''}\n{(body or '')[:500]}"
    keys: set[str] = set()
    keys.update(m.group(0) for m in _KOREAN_ORG_RE.finditer(text))
    keys.update(m.group(1) for m in _PROJECT_RE.finditer(text))
    keys.update(m.group(0) for m in _ENGLISH_NAME_RE.finditer(title or ""))
    return sorted(k.strip() for k in keys if len(k.strip()) >= 2)[:20]


def _to_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def fetch_cluster_candidates(
    conn: psycopg.Connection, lookback_days: int = 7, limit: int = 300
) -> list[dict]:
    """비교 후보 클러스터를 한 번에 읽는다 (배치당 1회 호출용).

    2026-08-20 성능 수리: 종전에는 assign_to_cluster가 **항목마다** 이 300행
    질의를 다시 실행했다. GitHub 러너(미국)→Supabase(서울) 왕복이 커서
    클러스터링이 건당 ~1초 — 실제 유사도 계산(~2ms)의 500배를 DB 왕복에
    썼다. 호출자가 목록을 메모리에 들고 배치 중 새 클러스터를 덧붙이면
    동작은 동일하고 왕복만 배치당 1회가 된다.
    """
    with conn.cursor() as cur:
        # 병합으로 비워진 클러스터는 후보에서 제외 (외부 리뷰 4차)
        cur.execute(
            """
            SELECT id, title, event_date, entity_keys
            FROM content_clusters
            WHERE created_at > now() - make_interval(days => %s)
              AND merged_at IS NULL
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (lookback_days, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def assign_to_cluster(
    conn: psycopg.Connection,
    raw_item: dict,
    source_type: str | None,
    title_threshold: int = 85,
    lookback_days: int = 7,
    candidates: list[dict] | None = None,
    defer_commit: bool = False,
) -> tuple[str, bool]:
    """raw_item을 기존 클러스터에 합치거나 새 클러스터를 만든다.

    candidates: fetch_cluster_candidates가 만든 목록. 주면 재조회하지 않고,
    새 클러스터를 그 목록 맨 앞에 덧붙인다 (같은 배치의 뒷 항목이 앞 항목과
    묶일 수 있도록 — 종전의 매번 재조회와 같은 의미).
    defer_commit: 호출자가 여러 항목을 묶어 커밋할 때 True (collect 배치).

    반환: (cluster_id, 새 클러스터 여부)
    """
    title = raw_item.get("title") or ""
    item_date = _to_date(raw_item.get("published_at"))
    entities = extract_entity_keys(title, raw_item.get("clean_text"))

    if candidates is None:
        candidates = fetch_cluster_candidates(conn, lookback_days)

    for cluster in candidates:
        if is_same_event(
            title,
            cluster["title"] or "",
            item_date,
            cluster["event_date"],
            entities,
            list(cluster["entity_keys"] or []),
            threshold=title_threshold,
        ):
            _add_member(
                conn, str(cluster["id"]), raw_item, source_type,
                defer_commit=defer_commit,
            )
            return str(cluster["id"]), False

    # 새 클러스터 생성
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO content_clusters
              (representative_raw_item_id, title, event_date, entity_keys, member_count)
            VALUES (%s, %s, %s, %s, 1)
            RETURNING id
            """,
            (raw_item["id"], title, item_date, entities),
        )
        cluster_id = str(cur.fetchone()["id"])
        cur.execute(
            """
            INSERT INTO cluster_members (cluster_id, raw_item_id, is_representative)
            VALUES (%s, %s, true)
            """,
            (cluster_id, raw_item["id"]),
        )
    if not defer_commit:
        conn.commit()
    # 메모리 후보 목록 최신화 — 같은 배치의 뒷 항목이 이 클러스터와 묶이게
    candidates.insert(0, {
        "id": cluster_id,
        "title": title,
        "event_date": item_date,
        "entity_keys": entities,
    })
    return cluster_id, True


def _add_member(
    conn: psycopg.Connection, cluster_id: str, raw_item: dict,
    source_type: str | None, defer_commit: bool = False,
) -> None:
    """기존 클러스터에 합류. 더 공식적인 출처가 오면 대표를 교체한다 (tasks §6.3)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cluster_members (cluster_id, raw_item_id, is_representative)
            VALUES (%s, %s, false)
            ON CONFLICT (raw_item_id) DO NOTHING
            """,
            (cluster_id, raw_item["id"]),
        )
        if cur.rowcount == 0:
            if not defer_commit:
                conn.commit()
            return

        cur.execute(
            "UPDATE content_clusters SET member_count = member_count + 1 WHERE id = %s",
            (cluster_id,),
        )

        # 현재 대표의 출처 유형 확인
        cur.execute(
            """
            SELECT cm.raw_item_id, s.source_type
            FROM cluster_members cm
            JOIN raw_items ri ON ri.id = cm.raw_item_id
            LEFT JOIN sources s ON s.id = ri.source_id
            WHERE cm.cluster_id = %s AND cm.is_representative = true
            """,
            (cluster_id,),
        )
        rep = cur.fetchone()
        new_rank = REPRESENTATIVE_RANK.get(source_type or "", 3)
        current_rank = REPRESENTATIVE_RANK.get(rep["source_type"] or "", 3) if rep else 99

        if rep is None or new_rank < current_rank:
            cur.execute(
                "UPDATE cluster_members SET is_representative = false WHERE cluster_id = %s",
                (cluster_id,),
            )
            cur.execute(
                """
                UPDATE cluster_members SET is_representative = true
                WHERE cluster_id = %s AND raw_item_id = %s
                """,
                (cluster_id, raw_item["id"]),
            )
            cur.execute(
                "UPDATE content_clusters SET representative_raw_item_id = %s WHERE id = %s",
                (raw_item["id"], cluster_id),
            )
            # 대표 출처가 바뀌면 완료된 분석도 다시 큐에 올린다 (외부 리뷰 P1-7)
            cur.execute(
                """
                UPDATE analysis_jobs
                SET status = 'PENDING', available_at = now(),
                    locked_at = NULL, locked_by = NULL,
                    last_error_code = NULL,
                    last_error_message = '대표 출처 교체로 재분석 예약'
                WHERE cluster_id = %s AND job_type = 'ARTICLE'
                  AND status IN ('DONE', 'FAILED', 'CANCELLED')
                """,
                (cluster_id,),
            )
    if not defer_commit:
        conn.commit()
