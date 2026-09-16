"""분석 결과 저장과 자동 게시 (tasks §9.3~9.5).

게시 조건 (설계 §13):
    is_robot_related = true AND validation_status IN (PASS, WARN)
    AND verified_facts 1개 이상
사람의 사전 승인 없이 게시한다 (FR-012 발행 정책과 동일 원칙).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import psycopg

from .config import Settings
from .db import load_app_settings
from .policy_normalize import normalize_policy_meta
from .schemas import (
    KIRO_CONTEXT_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    ArticleAnalysis,
    build_search_text,
)

# Gemini 응답 원문 저장 상한 (기존값 유지)
RAW_RESPONSE_MAX_CHARS = 20000


def raw_response_to_store(
    raw_response: str, validation_status: str, keep_raw_response: bool
) -> str | None:
    """analyses.raw_response에 저장할 JSON 문자열 (순수 함수 — 단위 테스트 대상).

    응답 원문(실측 26 MB)을 읽는 코드는 없다 — 파싱된 필드만 화면·검색이 쓴다.
    keep_raw_response=false면 PASS 분석은 저장하지 않고, PASS가 아닌 것
    (WARN·FAIL)은 왜 그렇게 판정됐는지 추적하기 위해 보존한다 (2026-09-08).
    """
    if not keep_raw_response and validation_status == "PASS":
        return None
    return json.dumps(
        {"text": raw_response[:RAW_RESPONSE_MAX_CHARS]}, ensure_ascii=False
    )


# 프로세스당 1회만 읽는다 — save_analysis 호출자(analyze.py)가 Settings를
# 넘기지 않으므로 여기서 env > app_settings > 기본값 순으로 직접 해석한다.
_keep_raw_response_cache: bool | None = None


def _keep_raw_response(conn: psycopg.Connection) -> bool:
    global _keep_raw_response_cache
    if _keep_raw_response_cache is None:
        settings = Settings.from_env()
        try:
            settings.apply_app_settings(load_app_settings(conn))
        except Exception as e:  # noqa: BLE001 — 설정 조회 실패는 기본값(저장 안 함)
            conn.rollback()
            print(f"[publish] 보존 설정 조회 실패(기본값 사용): {e}")
        _keep_raw_response_cache = settings.keep_raw_response
    return _keep_raw_response_cache


def save_analysis(
    conn: psycopg.Connection,
    cluster_id: str,
    analysis: ArticleAnalysis,
    validation_status: str,
    model_name: str,
    raw_response: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> str:
    """analyses 행 저장. 기존 분석은 삭제하지 않는다 (tasks §9.3)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO analyses (
              cluster_id, model_name, prompt_version, schema_version,
              is_robot_related, category, region, robot_field,
              importance, evidence_level, kiro_relevance,
              kiro_relevance_axes, kiro_relevance_reason,
              kiro_watchpoints, kiro_context_version,
              display_title, one_line_summary,
              verified_facts, numbers_and_dates,
              ai_interpretation, kiro_implication, limitations,
              keywords, policy_meta, raw_response,
              input_token_count, output_token_count, validation_status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                cluster_id,
                model_name,
                PROMPT_VERSION,
                SCHEMA_VERSION,
                analysis.is_robot_related,
                analysis.category,
                analysis.region,
                analysis.robot_field,
                analysis.importance,
                analysis.evidence_level,
                analysis.kiro_relevance,
                json.dumps(analysis.kiro_relevance_axes, ensure_ascii=False),
                analysis.kiro_relevance_reason or None,
                json.dumps(analysis.kiro_watchpoints, ensure_ascii=False),
                KIRO_CONTEXT_VERSION,
                analysis.display_title or None,
                analysis.one_line_summary or None,
                json.dumps(analysis.verified_facts, ensure_ascii=False),
                json.dumps(
                    [n.model_dump() for n in analysis.numbers_and_dates],
                    ensure_ascii=False,
                ),
                analysis.ai_interpretation or None,
                analysis.kiro_implication or None,
                analysis.limitations or None,
                json.dumps(analysis.keywords, ensure_ascii=False),
                json.dumps(analysis.policy_meta.model_dump(), ensure_ascii=False)
                if analysis.policy_meta
                else None,
                raw_response_to_store(
                    raw_response, validation_status, _keep_raw_response(conn)
                ),
                input_tokens,
                output_tokens,
                validation_status,
            ),
        )
        analysis_id = str(cur.fetchone()["id"])
    conn.commit()
    return analysis_id


# 게시 단계 2차 중복 병합 (사용자 피드백 + 외부 리뷰 3·4차 강화)
# 한국어 제목은 기업명 몇 글자만 다르고 나머지가 동일한 경우가 많아
# recall보다 precision을 우선한다 — 잘못 합치면 정보가 사라진다.
MERGE_STRONG_SIMILARITY = 0.85   # 날짜가 확인될 때만 단독 병합 가능
MERGE_WEAK_SIMILARITY = 0.45     # 이 이상 + 기관·기업명 일치 필수
MERGE_WINDOW_DAYS = 7
MERGE_DATE_DIFF_DAYS = 3
# 후보 검색 시 분류(카테고리·로봇 분야) 불일치를 무시하는 제목 유사도 하한.
# 병합 여부 자체는 여전히 merge_decision(유사도+기관+날짜)이 판정한다.
MERGE_CLASS_BYPASS_SIMILARITY = 0.55


def merge_decision(
    similarity: float,
    shares_entity: bool,
    date_diff_days: int | None,
) -> bool:
    """병합 여부 판정 (순수 함수 — 단위 테스트 대상).

    - 유사도 0.45 미만: 병합 금지
    - 발표일 차이 3일 초과: 병합 금지
    - 유사도 0.85 이상: 발표일이 확인된 경우에만 기관 불일치 허용
    - 그 외(0.45~0.85) 및 날짜 미상: 기관·기업·정책명 일치 필수
    """
    if similarity < MERGE_WEAK_SIMILARITY:
        return False
    if date_diff_days is not None and date_diff_days > MERGE_DATE_DIFF_DAYS:
        return False
    if similarity >= MERGE_STRONG_SIMILARITY and date_diff_days is not None:
        return True
    return shares_entity


def _merge_into_existing(
    conn: psycopg.Connection, cluster_id: str, analysis: ArticleAnalysis
) -> str | None:
    """같은 사건을 다룬 기존 게시물이 있으면 클러스터를 병합한다.

    후보는 표시 중(is_visible)이고 지역이 같은 최근 게시물. 분류(카테고리·
    로봇 분야)까지 같아야 하지만, 제목 유사도가 충분히 높으면 분류가 달라도
    후보로 허용한다 — AI가 같은 행사를 정책/산업 등으로 흔들리게 분류하면
    병합이 통째로 불발되는 문제(2026-08-07 부산로봇경진대회 6중 게시) 방지.
    병합 시 대표 출처 재선정, 관련 출처 수 갱신, 수동 등록 완료 처리,
    운영 이력 기록까지 한 트랜잭션에서 수행한다.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id, p.cluster_id, p.title, p.source_published_at,
                   p.published_at, similarity(p.title, %s) AS sim,
                   c.entity_keys
            FROM published_items p
            JOIN content_clusters c ON c.id = p.cluster_id
            WHERE p.is_visible = true
              AND p.region = %s
              AND (
                    (p.category = %s AND p.robot_field = %s)
                    OR similarity(p.title, %s) >= %s
                  )
              AND p.cluster_id != %s
              AND p.published_at > now() - make_interval(days => %s)
            ORDER BY sim DESC
            LIMIT 1
            """,
            (
                analysis.display_title,
                analysis.region,
                analysis.category,
                analysis.robot_field,
                analysis.display_title,
                MERGE_CLASS_BYPASS_SIMILARITY,
                cluster_id,
                MERGE_WINDOW_DAYS,
            ),
        )
        candidate = cur.fetchone()
        if not candidate:
            return None

        # 기관·기업·정책명 후보 겹침 (1차 클러스터링과 동일 기준)
        cur.execute(
            "SELECT entity_keys, event_date FROM content_clusters WHERE id = %s",
            (cluster_id,),
        )
        own = cur.fetchone()
        own_entities = list(own["entity_keys"] or []) if own else []
        cand_entities = list(candidate["entity_keys"] or [])
        shares = bool(
            {e.lower() for e in own_entities} & {e.lower() for e in cand_entities}
        )

        # 발표일 차이 (원문 발표일 우선, 미상이면 게시일)
        own_date = own["event_date"] if own else None
        cand_dt = candidate["source_published_at"] or candidate["published_at"]
        cand_date = cand_dt.date() if cand_dt else None
        date_diff = (
            abs((own_date - cand_date).days)
            if own_date and cand_date
            else None
        )

        if not merge_decision(float(candidate["sim"]), shares, date_diff):
            return None

        target_cluster = str(candidate["cluster_id"])

        # 기존 대표를 기억해 두고, 재선정 후 변경 여부로 재분석을 결정한다
        cur.execute(
            "SELECT representative_raw_item_id FROM content_clusters WHERE id = %s",
            (target_cluster,),
        )
        prev_rep_row = cur.fetchone()
        prev_rep_id = prev_rep_row["representative_raw_item_id"] if prev_rep_row else None

        # 1) 구성원 이동
        cur.execute(
            """
            UPDATE cluster_members
            SET cluster_id = %s, is_representative = false
            WHERE cluster_id = %s
            """,
            (target_cluster, cluster_id),
        )

        # 1-1) 흡수된 클러스터에 병합 표식 — 1차 클러스터링 후보에서 제외되고
        #      분석 이력·병합 추적은 보존된다 (외부 리뷰 4차)
        cur.execute(
            """
            UPDATE content_clusters
            SET merged_into_cluster_id = %s, merged_at = now()
            WHERE id = %s
            """,
            (target_cluster, cluster_id),
        )

        # 2) 대표 출처 재선정 — 새로 온 공식자료가 더 우선이면 교체 (tasks §6.3)
        cur.execute(
            """
            SELECT cm.raw_item_id, ri.url, ri.published_at,
                   coalesce(s.name, '수동 등록') AS source_name,
                   coalesce(s.source_type, '일반 언론') AS source_type
            FROM cluster_members cm
            JOIN raw_items ri ON ri.id = cm.raw_item_id
            LEFT JOIN sources s ON s.id = ri.source_id
            WHERE cm.cluster_id = %s
            """,
            (target_cluster,),
        )
        members = cur.fetchall()
        from .clustering import REPRESENTATIVE_RANK

        best = min(
            members,
            key=lambda m: (
                REPRESENTATIVE_RANK.get(m["source_type"], 3),
                m["published_at"] or datetime.max.replace(tzinfo=timezone.utc),
            ),
        )
        cur.execute(
            "UPDATE cluster_members SET is_representative = (raw_item_id = %s) WHERE cluster_id = %s",
            (best["raw_item_id"], target_cluster),
        )
        cur.execute(
            """
            UPDATE content_clusters
            SET representative_raw_item_id = %s, member_count = %s
            WHERE id = %s
            """,
            (best["raw_item_id"], len(members), target_cluster),
        )

        # 3) 기존 게시물 메타 갱신 (대표 출처·발표일·관련 출처 수)
        cur.execute(
            """
            UPDATE published_items
            SET related_source_count = %s,
                representative_url = %s,
                representative_source_name = %s,
                source_published_at = %s
            WHERE id = %s
            """,
            (
                len(members), best["url"], best["source_name"],
                best["published_at"], candidate["id"],
            ),
        )

        # 3-1) 대표 출처가 실제로 바뀌었으면 새 대표 기준으로 재분석 예약
        #      — 화면의 사실·해석이 옛 기사 기반으로 남지 않게 (외부 리뷰 4차)
        if prev_rep_id != best["raw_item_id"]:
            cur.execute(
                """
                UPDATE analysis_jobs
                SET status = 'PENDING', available_at = now(),
                    locked_at = NULL, locked_by = NULL,
                    last_error_code = NULL,
                    last_error_message = '2차 병합 대표 출처 변경으로 재분석'
                WHERE cluster_id = %s AND job_type = 'ARTICLE'
                  AND status IN ('DONE', 'FAILED', 'CANCELLED')
                """,
                (target_cluster,),
            )

        # 4) 수동 등록 완료 처리 (이동된 구성원 포함)
        cur.execute(
            """
            UPDATE manual_submissions ms
            SET status = 'DONE', completed_at = now()
            FROM cluster_members cm
            JOIN raw_items ri ON ri.id = cm.raw_item_id
            WHERE cm.cluster_id = %s
              AND ri.manual_submission_id = ms.id
              AND ms.status = 'PROCESSING'
            """,
            (target_cluster,),
        )

        # 5) 병합 이력 기록
        cur.execute(
            """
            INSERT INTO operation_events
              (event_type, target_table, target_id, reason, detail)
            VALUES ('OTHER', 'published_items', %s, '2차 중복 병합(자동)', %s)
            """,
            (
                candidate["id"],
                json.dumps(
                    {
                        "merged_cluster_id": cluster_id,
                        "similarity": round(float(candidate["sim"]), 3),
                        "shares_entity": shares,
                        "new_title": analysis.display_title,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
    conn.commit()
    print(
        f"[publish] 중복 병합(유사도 {candidate['sim']:.2f}, 기관일치 {shares}): "
        f"'{analysis.display_title}' → '{candidate['title']}'"
    )
    return str(candidate["id"])


def publish_analysis(
    conn: psycopg.Connection,
    cluster_id: str,
    analysis_id: str,
    analysis: ArticleAnalysis,
) -> str | None:
    """published_items upsert + policy_details 정규화. 게시 ID를 반환한다."""
    if not analysis.is_robot_related:
        return None

    # 2차 중복 병합: 이미 게시된 같은 사건이 있으면 흡수하고 게시하지 않음
    merged = _merge_into_existing(conn, cluster_id, analysis)
    if merged:
        return merged

    # 대표 출처 정보 조회
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ri.url, ri.published_at, ri.title,
                   coalesce(s.name, '수동 등록') AS source_name,
                   (SELECT count(*) FROM cluster_members cm2 WHERE cm2.cluster_id = %s)
                     AS member_count
            FROM content_clusters c
            JOIN raw_items ri ON ri.id = c.representative_raw_item_id
            LEFT JOIN sources s ON s.id = ri.source_id
            WHERE c.id = %s
            """,
            (cluster_id, cluster_id),
        )
        rep = cur.fetchone()
    if rep is None:
        return None

    search_text = build_search_text(analysis)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO published_items (
              cluster_id, current_analysis_id, title,
              category, region, robot_field,
              importance, evidence_level, kiro_relevance, kiro_axes,
              source_published_at, representative_source_name,
              representative_url, related_source_count, search_text,
              one_line_summary, kiro_implication_excerpt
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (cluster_id) DO UPDATE SET
              current_analysis_id = EXCLUDED.current_analysis_id,
              title = EXCLUDED.title,
              category = EXCLUDED.category,
              region = EXCLUDED.region,
              robot_field = EXCLUDED.robot_field,
              importance = EXCLUDED.importance,
              evidence_level = EXCLUDED.evidence_level,
              kiro_relevance = EXCLUDED.kiro_relevance,
              kiro_axes = EXCLUDED.kiro_axes,
              representative_source_name = EXCLUDED.representative_source_name,
              representative_url = EXCLUDED.representative_url,
              related_source_count = EXCLUDED.related_source_count,
              search_text = EXCLUDED.search_text,
              one_line_summary = EXCLUDED.one_line_summary,
              kiro_implication_excerpt = EXCLUDED.kiro_implication_excerpt
            RETURNING id
            """,
            (
                cluster_id,
                analysis_id,
                analysis.display_title,
                analysis.category,
                analysis.region,
                analysis.robot_field,
                analysis.importance,
                analysis.evidence_level,
                analysis.kiro_relevance,
                # 업무축 사본(C7) — 화면 필터가 analyses 대신 이 컬럼을 읽는다 (금고 카드 유지)
                json.dumps(analysis.kiro_relevance_axes, ensure_ascii=False),
                rep["published_at"],
                rep["source_name"],
                rep["url"],
                rep["member_count"],
                search_text,
                analysis.one_line_summary or None,
                (analysis.kiro_implication or "")[:200] or None,
            ),
        )
        published_id = str(cur.fetchone()["id"])

        # 정책 메타데이터 정규화 (FR-010, tasks §9.4)
        policy_row = normalize_policy_meta(
            analysis.policy_meta.model_dump() if analysis.policy_meta else None
        )
        if policy_row:
            cur.execute(
                """
                INSERT INTO policy_details (
                  published_item_id, policy_name, project_name,
                  ministries, organizations, budget_text, budget_amount_krw,
                  project_start_date, project_end_date, support_targets,
                  announcement_status, application_deadline, target_region
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (published_item_id) DO UPDATE SET
                  policy_name = EXCLUDED.policy_name,
                  project_name = EXCLUDED.project_name,
                  ministries = EXCLUDED.ministries,
                  organizations = EXCLUDED.organizations,
                  budget_text = EXCLUDED.budget_text,
                  budget_amount_krw = EXCLUDED.budget_amount_krw,
                  project_start_date = EXCLUDED.project_start_date,
                  project_end_date = EXCLUDED.project_end_date,
                  support_targets = EXCLUDED.support_targets,
                  announcement_status = EXCLUDED.announcement_status,
                  application_deadline = EXCLUDED.application_deadline,
                  target_region = EXCLUDED.target_region
                """,
                (
                    published_id,
                    policy_row["policy_name"],
                    policy_row["project_name"],
                    policy_row["ministries"],
                    policy_row["organizations"],
                    policy_row["budget_text"],
                    policy_row["budget_amount_krw"],
                    policy_row["project_start_date"],
                    policy_row["project_end_date"],
                    policy_row["support_targets"],
                    policy_row["announcement_status"],
                    policy_row["application_deadline"],
                    policy_row["target_region"],
                ),
            )

        # 수동 등록 URL이 게시까지 완료되면 상태를 DONE으로 (외부 리뷰 P1-8 후속)
        cur.execute(
            """
            UPDATE manual_submissions ms
            SET status = 'DONE', completed_at = now()
            FROM cluster_members cm
            JOIN raw_items ri ON ri.id = cm.raw_item_id
            WHERE cm.cluster_id = %s
              AND ri.manual_submission_id = ms.id
              AND ms.status = 'PROCESSING'
            """,
            (cluster_id,),
        )
    conn.commit()
    return published_id
