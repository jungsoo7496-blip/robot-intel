"""정책·동향 보고서 Gemini 배치 분석 (스펙 §18~27).

application-level batching: generateContent 1콜에 보고서 4건의 메타데이터를
넣어 구조화 출력을 받는다 — RPD 소모는 콜 수 기준이므로
6콜 × 4건 = 일 최대 24건. PDF/HWP 전문은 보내지 않는다 (스펙 §25).

모델: 주간 브리프와 같은 Flash 계열(RPD 20 공유) — 기존 브리프 소비는
주 3~4콜뿐이라 일 6콜 예산은 안전 (2026-08-08 감사).

실행: python -m kiro_batch.analyze_reports
환경: REPORT_AI_MODEL, REPORT_AI_DAILY_CALL_LIMIT(6), REPORT_AI_ITEMS_PER_CALL(4),
      REPORT_AI_MAX_CALLS_PER_RUN(기본 = 남은 일일 예산),
      REPORT_AI_MAX_ITEMS_PER_SOURCE_PER_DAY(8, 스펙 §19 fairness)
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

from .config import Settings
from .db import connect, load_app_settings
from .gemini_provider import (
    GeminiProvider,
    InvalidResponseError,
    ProviderServerError,
    RateLimitError,
)
from .queue import record_gemini_call
from .quota import quota_date_pt
from .usage import UsageRecorder

ANALYSIS_VERSION = "report_v1"
PROMPT_VERSION = "report_analysis_v1"
CONTEXT_VERSION = "kiro_public_context_v1"
PURPOSE = "REPORT_ANALYSIS"
MAX_ATTEMPTS = 3
ABSTRACT_MAX = 2000

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
_PT = ZoneInfo("America/Los_Angeles")

RELEVANCE_VALUES = {"DIRECT", "RELATED", "WEAK", "EXCLUDE"}
REPORT_TYPES = {
    "정책·전략", "산업·시장", "기술·R&D", "통계·실태조사", "법·제도·규제",
    "로드맵·계획", "사업·성과·평가", "동향·브리프", "학술·연구", "기타",
}
ROBOT_FIELDS = {
    "휴머노이드·피지컬 AI", "제조·산업용 로봇", "서비스 로봇", "물류 로봇",
    "의료·돌봄 로봇", "농업 로봇", "국방·재난 로봇", "해양·특수환경 로봇",
    "핵심 부품·소프트웨어", "기타",
}
KIRO_RELEVANCE_VALUES = {"직접", "간접", "낮음"}
KIRO_AXES = {
    "정책·전략", "R&D 기획", "연구개발", "실증·시험평가",
    "기업지원·사업화", "인재양성", "협력·생태계", "전략방향",
}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    return int(raw) if raw.isdigit() else default


def next_pt_quota_reset_utc(now_utc: datetime | None = None) -> datetime:
    """다음 PT 자정(쿼터 리셋)의 UTC 시각 — 429 시 DEFERRED available_at."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    pt_now = now_utc.astimezone(_PT)
    next_day = (pt_now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return next_day.astimezone(timezone.utc)


def claim_jobs(
    conn: psycopg.Connection, batch_size: int, per_source_cap: int
) -> list[dict]:
    """분석 대기 job을 문서·출처 메타데이터와 함께 잠근다.

    - 접근 가능한(usable) 문서 우선 — 쿼터가 작으므로 공개될 문서부터
    - 소스당 일일 상한(스펙 §19 fairness): 오늘 이미 분석된 건수를 빼고 잡는다
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH todays AS (
              SELECT o.source_id, count(*) AS done_today
              FROM report_analyses a
              JOIN report_documents d ON d.id = a.report_document_id
              LEFT JOIN report_occurrences o ON o.id = d.preferred_occurrence_id
              WHERE a.created_at > now() - interval '24 hours'
              GROUP BY o.source_id
            ),
            ranked AS (
              SELECT j.id AS job_id, j.attempt_count, j.input_hash,
                     d.id AS document_id, d.canonical_title, d.institution,
                     d.best_abstract, d.published_date, d.published_year,
                     o.source_report_type, o.source_keywords, o.access_status,
                     s.name AS source_name, s.id AS source_id,
                     row_number() OVER (
                       PARTITION BY s.id ORDER BY j.priority, j.created_at
                     ) AS rn,
                     COALESCE(t.done_today, 0) AS done_today
              FROM report_analysis_jobs j
              JOIN report_documents d ON d.id = j.report_document_id
              LEFT JOIN report_occurrences o ON o.id = d.preferred_occurrence_id
              LEFT JOIN report_sources s ON s.id = o.source_id
              LEFT JOIN todays t ON t.source_id = s.id
              WHERE j.status IN ('PENDING', 'RETRY') AND j.available_at <= now()
            )
            SELECT * FROM ranked
            WHERE rn + done_today <= %s
            ORDER BY (access_status IN ('DIRECT_DOWNLOAD','SOURCE_DOWNLOAD','VIEW_ONLY')) DESC,
                     rn
            LIMIT %s
            """,
            (per_source_cap, batch_size),
        )
        rows = cur.fetchall()
        claimed_ids: set = set()
        if rows:
            # 상태 가드 재확인으로 잠금 (동시 실행 대비 — RETURNING된 것만 진행)
            cur.execute(
                """
                UPDATE report_analysis_jobs
                SET status = 'PROCESSING', locked_at = now(), locked_by = %s,
                    attempt_count = attempt_count + 1, updated_at = now()
                WHERE id = ANY(%s) AND status IN ('PENDING', 'RETRY')
                RETURNING id
                """,
                (
                    os.environ.get("GITHUB_RUN_ID") or "local",
                    [r["job_id"] for r in rows],
                ),
            )
            claimed_ids = {r["id"] for r in cur.fetchall()}
    conn.commit()
    return [r for r in rows if r["job_id"] in claimed_ids]


def build_prompt(kiro_context: str, jobs: list[dict]) -> str:
    template = (_PROMPTS_DIR / "report_analysis_v1.txt").read_text(encoding="utf-8")
    items = []
    for j in jobs:
        keywords = j["source_keywords"]
        if isinstance(keywords, str):
            try:
                keywords = json.loads(keywords)
            except ValueError:
                keywords = []
        items.append(
            {
                "id": str(j["document_id"]),
                "title": j["canonical_title"],
                "institution": j["institution"],
                "published": (
                    j["published_date"].isoformat()
                    if j["published_date"]
                    else (str(j["published_year"]) if j["published_year"] else None)
                ),
                "source": j["source_name"],
                "source_report_type": j["source_report_type"],
                "source_keywords": keywords or [],
                "abstract": (j["best_abstract"] or "")[:ABSTRACT_MAX] or None,
            }
        )
    return template.replace("{{KIRO_PUBLIC_CONTEXT}}", kiro_context).replace(
        "{{ITEMS_JSON}}", json.dumps(items, ensure_ascii=False, indent=1)
    )


def validate_item(raw: dict) -> dict | None:
    """항목 1건의 enum·형식 검증. 실패하면 None (해당 job은 RETRY)."""
    if not isinstance(raw, dict):
        return None
    relevance = raw.get("robot_relevance")
    if relevance not in RELEVANCE_VALUES:
        return None
    report_type = raw.get("report_type")
    if report_type not in REPORT_TYPES:
        report_type = "기타"
    primary = raw.get("primary_robot_field")
    if primary not in ROBOT_FIELDS:
        primary = "기타"
    fields = [f for f in (raw.get("robot_fields") or []) if f in ROBOT_FIELDS]
    kiro_rel = raw.get("kiro_relevance")
    if kiro_rel not in KIRO_RELEVANCE_VALUES:
        kiro_rel = "낮음"
    axes = [a for a in (raw.get("kiro_relevance_axes") or []) if a in KIRO_AXES]
    summary = str(raw.get("summary") or "").strip()
    if not summary:
        return None
    return {
        "robot_relevance": relevance,
        "relevance_reason": str(raw.get("relevance_reason") or "")[:1000],
        "report_type": report_type,
        "primary_robot_field": primary,
        "robot_fields": fields or [primary],
        "summary": summary[:4000],
        "kiro_relevance": kiro_rel,
        "kiro_relevance_axes": axes,
        "kiro_reason": str(raw.get("kiro_reason") or "")[:1000],
        "keywords": [str(k)[:60] for k in (raw.get("keywords") or [])][:8],
        "limitations": str(raw.get("limitations") or "")[:1000],
    }


def save_analysis(
    conn: psycopg.Connection,
    job: dict,
    item: dict,
    requested_model: str,
    served_model: str | None,
) -> None:
    """분석 저장 + document 상태·공개 여부 갱신 + job DONE."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO report_analyses (
              report_document_id, analysis_version, prompt_version,
              context_version, requested_model, served_model,
              robot_relevance, relevance_reason, report_type,
              primary_robot_field, robot_fields, summary,
              kiro_relevance, kiro_relevance_axes, kiro_reason,
              keywords, limitations, input_hash
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                job["document_id"], ANALYSIS_VERSION, PROMPT_VERSION,
                CONTEXT_VERSION, requested_model, served_model,
                item["robot_relevance"], item["relevance_reason"],
                item["report_type"], item["primary_robot_field"],
                json.dumps(item["robot_fields"], ensure_ascii=False),
                item["summary"], item["kiro_relevance"],
                json.dumps(item["kiro_relevance_axes"], ensure_ascii=False),
                item["kiro_reason"],
                json.dumps(item["keywords"], ensure_ascii=False),
                item["limitations"], job["input_hash"],
            ),
        )
        analysis_id = cur.fetchone()["id"]

        # 공개 조건 (스펙 §30): usable access + DIRECT/RELATED + DONE.
        # manual_override 문서는 운영자가 정한 노출 상태를 건드리지 않는다.
        cur.execute(
            """
            UPDATE report_documents d
            SET current_analysis_id = %s,
                analysis_status = 'DONE',
                is_visible = CASE WHEN d.manual_override THEN d.is_visible ELSE (
                  %s IN ('DIRECT', 'RELATED') AND EXISTS (
                    SELECT 1 FROM report_occurrences o
                    WHERE o.document_id = d.id AND o.access_status IN
                      ('DIRECT_DOWNLOAD', 'SOURCE_DOWNLOAD', 'VIEW_ONLY')
                  )
                ) END,
                updated_at = now()
            WHERE d.id = %s
            """,
            (analysis_id, item["robot_relevance"], job["document_id"]),
        )
        cur.execute(
            """
            UPDATE report_analysis_jobs
            SET status = 'DONE', locked_at = NULL, locked_by = NULL,
                last_error_code = NULL, last_error_message = NULL,
                updated_at = now()
            WHERE id = %s
            """,
            (job["job_id"],),
        )
    conn.commit()


def mark_jobs(
    conn: psycopg.Connection,
    job_ids: list,
    status: str,
    available_at_utc: datetime | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    if not job_ids:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE report_analysis_jobs
            SET status = %s,
                available_at = COALESCE(%s, available_at),
                locked_at = NULL, locked_by = NULL,
                last_error_code = %s, last_error_message = %s,
                updated_at = now()
            WHERE id = ANY(%s)
            """,
            (status, available_at_utc, error_code,
             (error_message or "")[:500] or None, job_ids),
        )
        if status == "FAILED":
            cur.execute(
                """
                UPDATE report_documents SET analysis_status = 'FAILED',
                  updated_at = now()
                WHERE id IN (
                  SELECT report_document_id FROM report_analysis_jobs
                  WHERE id = ANY(%s)
                ) AND analysis_status <> 'DONE'
                """,
                (job_ids,),
            )
    conn.commit()


def retry_or_fail(
    conn: psycopg.Connection, jobs: list[dict], code: str, message: str
) -> None:
    """일시 오류: RETRY(백오프), MAX_ATTEMPTS 도달 시 FAILED (무한 retry 금지)."""
    retry_ids = [j["job_id"] for j in jobs if j["attempt_count"] + 1 < MAX_ATTEMPTS]
    fail_ids = [j["job_id"] for j in jobs if j["attempt_count"] + 1 >= MAX_ATTEMPTS]
    backoff = datetime.now(timezone.utc) + timedelta(minutes=30)
    mark_jobs(conn, retry_ids, "RETRY", backoff, code, message)
    mark_jobs(conn, fail_ids, "FAILED", None, code, message)


def main() -> int:
    settings = Settings.from_env()
    settings.require("supabase_db_url", "gemini_api_key")

    conn = connect(settings.supabase_db_url)
    try:
        settings.apply_app_settings(load_app_settings(conn))

        model = os.environ.get("REPORT_AI_MODEL") or settings.brief_model
        daily_limit = _env_int("REPORT_AI_DAILY_CALL_LIMIT", 6)
        items_per_call = _env_int("REPORT_AI_ITEMS_PER_CALL", 4)
        per_source_cap = _env_int("REPORT_AI_MAX_ITEMS_PER_SOURCE_PER_DAY", 8)

        # 예산 계산용 쿼터일 — 실행 시작 시점 기준.
        # 기록은 quota_date를 쓰지 않는다: 호출 하나가 몇 초 걸려 KST 17시
        # 리셋을 걸칠 수 있고, 그러면 구글은 새 쿼터일로 세는데 우리만
        # 어제로 남아 다음 날 카운터가 실제보다 작아진다
        # (analyze.py의 결함 I와 같은 문제 — 2026-09-08 정정).
        quota_date = quota_date_pt()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) AS cnt FROM gemini_calls
                WHERE purpose = %s AND quota_date_pt = %s
                """,
                (PURPOSE, quota_date),
            )
            used_today = int(cur.fetchone()["cnt"])
        remaining = max(0, daily_limit - used_today)
        max_calls = min(
            remaining, _env_int("REPORT_AI_MAX_CALLS_PER_RUN", remaining)
        )
        if max_calls == 0:
            print(
                f"[analyze_reports] 일일 예산 소진 ({used_today}/{daily_limit}) — 종료"
            )
            return 0

        # 중단된 실행이 남긴 PROCESSING 잠금 복구 (2026-08-28 실측: 8/13·8/23
        # 크래시 잔재 8건이 영구 잠겨 있었다 — 그 문서들은 영원히 분석 안 됨)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE report_analysis_jobs
                SET status = 'RETRY', available_at = now(),
                    last_error_message = '중단된 실행 잠금 복구'
                WHERE status = 'PROCESSING'
                  AND updated_at < now() - interval '30 minutes'
                """
            )
            if cur.rowcount:
                print(f"[analyze_reports] 잠긴 PROCESSING {cur.rowcount}건 복구")
        conn.commit()

        kiro_context = (
            _PROMPTS_DIR / "context" / "kiro_public_context_v1.md"
        ).read_text(encoding="utf-8")
        provider = GeminiProvider(settings.gemini_api_key)
        usage = UsageRecorder(
            conn, "analyze_reports", note=f"{model} · 최대 {max_calls}콜"
        )

        analyzed = errors = 0
        status = "SUCCESS"
        current_model = model
        for call_no in range(max_calls):
            jobs = claim_jobs(conn, items_per_call, per_source_cap)
            if not jobs:
                print("[analyze_reports] 대기 job 없음 — 종료")
                break
            prompt = build_prompt(kiro_context, jobs)
            job_ids = [j["job_id"] for j in jobs]
            try:
                result = provider.analyze_article(prompt, current_model)
                usage.counts["api_calls"] += 1
                record_gemini_call(
                    conn, current_model, quota_date_pt(), "OK",
                    purpose=PURPOSE, requested_model=current_model,
                    served_model=result.served_model,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                )
            except RateLimitError:
                record_gemini_call(
                    conn, current_model, quota_date_pt(), "RATE_LIMITED", "429",
                    purpose=PURPOSE, requested_model=current_model,
                )
                mark_jobs(
                    conn, job_ids, "DEFERRED", next_pt_quota_reset_utc(),
                    "429", "쿼터 소진 — 다음 PT 쿼터일로 이월",
                )
                print("[analyze_reports] 429 — DEFERRED 처리 후 종료", file=sys.stderr)
                status = "PARTIAL"
                break
            except ProviderServerError as e:
                record_gemini_call(
                    conn, current_model, quota_date_pt(), "ERROR", "5xx",
                    purpose=PURPOSE, requested_model=current_model,
                )
                retry_or_fail(conn, jobs, "5xx", str(e))
                errors += 1
                status = "PARTIAL"
                # 같은 모델로 고집하지 않는다 (2026-08-28): Flash가 8/22부터
                # 6일째 503(과부하)인데 매 실행이 전 예산을 같은 모델에
                # 태우며 전멸했다. 서버 오류면 예비 모델로 갈아타고 계속한다.
                if current_model != settings.brief_fallback_model:
                    current_model = settings.brief_fallback_model
                    print(
                        f"[analyze_reports] 5xx — 예비 모델로 전환: {current_model}",
                        file=sys.stderr,
                    )
                continue
            except InvalidResponseError as e:
                record_gemini_call(
                    conn, current_model, quota_date_pt(), "OK",
                    purpose=PURPOSE, requested_model=current_model,
                )
                usage.counts["api_calls"] += 1
                retry_or_fail(conn, jobs, "INVALID_JSON", str(e))
                errors += 1
                status = "PARTIAL"
                continue

            returned = {
                str(i.get("id")): i
                for i in (result.data.get("items") or [])
                if isinstance(i, dict)
            }
            missing: list[dict] = []
            for job in jobs:
                item = validate_item(returned.get(str(job["document_id"]), {}))
                if item is None:
                    missing.append(job)
                    continue
                save_analysis(conn, job, item, current_model, result.served_model)
                analyzed += 1
            if missing:
                retry_or_fail(
                    conn, missing, "MISSING_ITEM",
                    "응답에 항목 누락 또는 형식 오류",
                )
                errors += len(missing)
                status = "PARTIAL"
            print(
                f"[analyze_reports] 콜 {call_no + 1}/{max_calls}: "
                f"{len(jobs)}건 요청 · {len(jobs) - len(missing)}건 저장"
            )
            if call_no + 1 < max_calls:
                time.sleep(2)

        usage.counts["analyzed"] = analyzed
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) AS cnt FROM report_analysis_jobs
                WHERE status IN ('PENDING', 'RETRY', 'DEFERRED')
                """
            )
            pending = int(cur.fetchone()["cnt"])
        usage.finish(status, remaining_pending=pending)
        print(
            f"[analyze_reports] 완료: 분석 {analyzed}건 · 오류 {errors}건 · "
            f"대기 {pending}건"
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
