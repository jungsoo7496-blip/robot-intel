"""기존 콘텐츠 재분석 큐 등록 (v1.1 — 외부 리뷰 지시 13항).

새 prompt/context 기준으로 재분석할 콘텐츠를 소량씩 큐에 올린다.
기존 분석은 삭제하지 않으며, 새 분석이 PASS하면 기존 버전관리
원칙(current_analysis_id 교체)에 따라 화면이 갱신된다.

실행:
    python -m kiro_batch.requeue_analysis --reason kiro-context-v1 --limit 20

우선순위:
    1. 공개(is_visible) + importance=높음
    2. 공개 + kiro_relevance=직접
    3. 최근 90일 콘텐츠
    4. 나머지

이미 article_analysis_v2 + kiro_public_context_v1로 분석된
클러스터는 중복 등록하지 않는다.
"""

from __future__ import annotations

import argparse

from .config import Settings
from .db import connect
from .schemas import KIRO_CONTEXT_VERSION, PROMPT_VERSION


def requeue(limit: int, reason: str, dry_run: bool = False) -> int:
    settings = Settings.from_env()
    settings.require("supabase_db_url")
    conn = connect(settings.supabase_db_url)

    with conn.cursor() as cur:
        cur.execute(
            """
            WITH candidates AS (
              SELECT p.cluster_id, p.title,
                     CASE
                       WHEN p.is_visible AND p.importance = '높음' THEN 1
                       WHEN p.is_visible AND p.kiro_relevance = '직접' THEN 2
                       WHEN p.published_at > now() - interval '90 days' THEN 3
                       ELSE 4
                     END AS tier
              FROM published_items p
              WHERE NOT EXISTS (
                -- 이미 새 기준으로 분석된 클러스터는 제외
                SELECT 1 FROM analyses a
                WHERE a.cluster_id = p.cluster_id
                  AND a.prompt_version = %s
                  AND a.kiro_context_version = %s
              )
            )
            SELECT c.cluster_id, c.title, c.tier
            FROM candidates c
            JOIN analysis_jobs j
              ON j.cluster_id = c.cluster_id AND j.job_type = 'ARTICLE'
            WHERE j.status IN ('DONE', 'FAILED', 'CANCELLED')
            ORDER BY c.tier ASC
            LIMIT %s
            """,
            (PROMPT_VERSION, KIRO_CONTEXT_VERSION, limit),
        )
        rows = cur.fetchall()

        if dry_run:
            for r in rows:
                print(f"  [tier {r['tier']}] {r['title'][:60]}")
            print(f"(dry-run) 재분석 대상 {len(rows)}건")
            conn.close()
            return len(rows)

        for r in rows:
            # priority 60 = 백필 전용 등급: 신규 수집(10~45)이 모두 소화된 뒤에만
            # 처리되도록 강등한다. 원래 우선순위를 물려받으면 created_at이 오래된
            # 백필이 신규 기사보다 먼저 처리되는 역전이 생긴다 (2026-08-07 확인).
            cur.execute(
                """
                UPDATE analysis_jobs
                SET status = 'PENDING', available_at = now(),
                    priority = 60,
                    locked_at = NULL, locked_by = NULL,
                    last_error_code = NULL,
                    last_error_message = %s
                WHERE cluster_id = %s AND job_type = 'ARTICLE'
                """,
                (f"재분석 요청: {reason}", r["cluster_id"]),
            )
            print(f"  [tier {r['tier']}] {r['title'][:60]}")
    conn.commit()
    conn.close()
    print(f"재분석 큐 등록 {len(rows)}건 (사유: {reason})")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="기존 콘텐츠 재분석 큐 등록")
    parser.add_argument("--reason", required=True, help="재분석 사유 (이력 기록용)")
    parser.add_argument("--limit", type=int, default=20, help="등록 최대 건수")
    parser.add_argument("--dry-run", action="store_true", help="대상만 출력")
    args = parser.parse_args()
    requeue(args.limit, args.reason, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
