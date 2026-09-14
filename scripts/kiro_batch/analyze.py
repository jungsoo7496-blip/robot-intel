"""AI 분석 배치 진입점 (설계 §9.5, tasks §7, §9).

실행: python -m kiro_batch.analyze

종료 조건:
    - 배치당 최대 건수 (폭주 방지용 — 평소에는 시간 예산이 먼저 걸린다.
      수동 실행이 workflow 입력 max_count로 지정하면 그 값이 그대로 쓰인다)
    - 내부 경과시간(시간대별 예산 — freshness.batch_budget_seconds)
    - PT 날짜 기준 일일 내부 목표 도달 (브리프 예약량 제외,
      야간에는 오전 예약분을 추가로 남김 — quota.nightly_soft_limit)
남은 작업은 큐에 보존되어 다음 배치로 이월된다.
"""

from __future__ import annotations

import math
import os
import socket
import sys
import threading
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings
from .db import connect, load_app_settings
from .gemini_provider import (
    AIProvider,
    GeminiProvider,
    InvalidResponseError,
    ProviderServerError,
    RateLimitError,
)
from .freshness import batch_budget_seconds, should_drain, starvation_slots
from .publish import publish_analysis, save_analysis
from .queue import (
    acquire_next_job,
    complete_job,
    count_pending,
    fail_job,
    record_gemini_call,
    recover_stale_locks,
    retry_job_after_rate_limit,
    todays_call_count,
)
from .quota import hours_until_quota_reset, nightly_soft_limit, quota_date_pt
from .ratelimit import RateLimiter
from .schemas import SchemaValidationError, validate_analysis
from .usage import UsageRecorder, close_stale_runs

# 연속 429가 이만큼 나오면 배치를 접는다 (남은 예산을 429로 태우지 않음)
MAX_CONSECUTIVE_RATE_LIMITS = 3

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
_PROMPT_PATH = _PROMPTS_DIR / "article_analysis_v2.txt"
# 브로슈어 PDF를 매번 보내지 않는다 — 검증된 압축 컨텍스트만 주입 (v1.1)
_KIRO_CONTEXT_PATH = _PROMPTS_DIR / "context" / "kiro_public_context_v1.md"


# 프롬프트·컨텍스트는 배치 내내 불변인데 매 건 디스크에서 읽고 있었다
@lru_cache(maxsize=1)
def _prompt_template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _kiro_context() -> str:
    return _KIRO_CONTEXT_PATH.read_text(encoding="utf-8")


def build_article_prompt(conn, cluster_id: str, settings: Settings) -> str | None:
    """대표 원문 + 관련 기사 제목으로 프롬프트를 구성한다 (tasks §9.1)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ri.title, ri.clean_text, ri.feed_summary, ri.published_at,
                   coalesce(s.name, '수동 등록') AS source_name,
                   coalesce(s.source_type, '일반 언론') AS source_type
            FROM content_clusters c
            JOIN raw_items ri ON ri.id = c.representative_raw_item_id
            LEFT JOIN sources s ON s.id = ri.source_id
            WHERE c.id = %s
            """,
            (cluster_id,),
        )
        rep = cur.fetchone()
        if rep is None:
            return None

        # 관련 기사 제목만 제한적으로 포함 (전문 중복 전송 금지, tasks §9.1)
        cur.execute(
            """
            SELECT ri.title
            FROM cluster_members cm
            JOIN raw_items ri ON ri.id = cm.raw_item_id
            WHERE cm.cluster_id = %s AND cm.is_representative = false
            LIMIT 5
            """,
            (cluster_id,),
        )
        related_titles = [r["title"] for r in cur.fetchall() if r["title"]]

    body = (rep["clean_text"] or rep["feed_summary"] or "").strip()
    if not body:
        return None
    body = body[: settings.clean_text_max_length]

    return (
        _prompt_template()
        .replace("{{KIRO_PUBLIC_CONTEXT}}", _kiro_context())
        .replace("{{TITLE}}", rep["title"] or "(제목 없음)")
        .replace("{{SOURCE_NAME}}", rep["source_name"])
        .replace("{{SOURCE_TYPE}}", rep["source_type"])
        .replace("{{PUBLISHED_AT}}", str(rep["published_at"] or "미상"))
        .replace("{{RELATED_TITLES}}", "\n".join(f"- {t}" for t in related_titles) or "(없음)")
        .replace("{{BODY}}", body)
    )


def resolved_models(settings: Settings) -> list[str]:
    """이번 배치가 실제로 쓸 모델 목록.

    수동 실행이 모델을 지정하면(ARTICLE_MODEL) 그 하나만 — 운영자 의도 존중.
    """
    if os.environ.get("ARTICLE_MODEL", "").strip():
        return [settings.article_model]
    models = [m.strip() for m in settings.article_models.split(",") if m.strip()]
    return models or [settings.article_model]


def runaway_cap_floor(
    budget_seconds: int, model_count: int, target_rpm: float, workers: int
) -> int:
    """건수 캡이 시간 예산보다 먼저 걸리지 않게 하는 최소 캡 (2026-09-08).

    캡은 폭주 방지용이고 처리량은 시간 예산이 정한다는 원칙의 강제 장치다.
    속도 상한은 RateLimiter가 모델당 target_rpm으로 잡으므로 배치 전체는
    분당 model_count × target_rpm건이 최대다. 여기에 시간 검사를 통과한
    워커들이 각자 마지막 한 건을 더 시작하는 초과분을 더한다.

    실측(09-08 09:16, 예산 720초): 캡 280에 걸려 279건에서 끝났다. 다만
    그 실행의 workflow_usage는 duration 758초 — 예산을 남긴 게 아니라 이미
    넘긴 뒤였다. 분당 24건이면 720초의 이론 최대치가 288건이므로 캡을 풀어
    더 얻을 수 있던 건 **많아야 9건**이다. 처리량이 아니라 '무엇이 배치를
    끝냈는지'를 위한 정리다 — 두 상한이 같이 걸리면 예산을 조정해도 효과를
    읽을 수 없다.
    """
    per_second = model_count * max(0.0, target_rpm) / 60.0
    overrun = model_count * max(1, workers)
    return math.ceil(max(0, budget_seconds) * per_second) + overrun


def _bump(usage: UsageRecorder, key: str, shared: "_Shared") -> None:
    with shared.counter_lock:
        usage.counts[key] += 1


def _record_call(
    conn, model: str, job_id: str, status: str, error_code: str | None = None
) -> None:
    """호출 1건을 기록한다 — 쿼터일은 '기록하는 지금' 기준으로 다시 잡는다.

    호출 하나가 5~7초 걸리므로 쿼터 리셋(UTC-8 자정 = KST 17시)을 걸친 채
    끝날 수 있다. 그때는 늦은 쪽(새 쿼터일)에 얹는다. 구글은 요청 시각으로
    세므로 이 한 건은 새 쿼터일에서 우리가 1건 더 세는 셈인데, 그래야
    "우리 카운터는 구글보다 크면 컸지 작지 않다"가 유지된다
    (config.article_daily_soft_limit 주석의 전제).
    """
    record_gemini_call(
        conn, model, quota_date_pt(), status, error_code=error_code, job_id=job_id
    )


class _Shared:
    """스트림들이 함께 보는 상태 (배치 전체 건수·잠금)."""

    def __init__(self) -> None:
        self.processed = 0
        self.counter_lock = threading.Lock()
        # 저장·게시는 직렬화한다. 두 스트림이 같은 사건 기사를 동시에 게시하면
        # 서로를 못 보고 둘 다 올라간다(_merge_into_existing은 '이미 게시된'
        # 것하고만 비교한다). Gemini 호출(5~7초)은 병렬로 두고 게시(1~2초)만
        # 줄 세우므로 처리량 손실은 거의 없다.
        self.publish_lock = threading.Lock()

    def take_slot(self, cap: int) -> int | None:
        """다음 처리 순번을 받는다. 정원이 찼으면 None."""
        with self.counter_lock:
            if self.processed >= cap:
                return None
            self.processed += 1
            return self.processed

    def give_back(self) -> None:
        with self.counter_lock:
            self.processed -= 1


def process_queue(
    conn,
    provider: AIProvider,
    settings: Settings,
    usage: UsageRecorder,
) -> None:
    """분석 큐를 처리한다. 모델이 여러 개면 스트림을 병렬로 돌린다.

    2026-08-11: 배치가 100% 시간예산에서 끝나는데(건수캡 도달 0회) 정작
    Gemini 쿼터는 27%만 쓰고 있었다. 무료 모델 2종이 서로 독립 쿼터 풀임을
    실측 확인(같은 날 445+403콜, 429 0건)했으므로, 같은 Actions 분 안에서
    두 스트림을 병렬로 돌려 처리량을 2배로 만든다. 쿼터는 모델별로 따로
    검사하므로 각각의 한도를 넘지 않는다.
    """
    models = resolved_models(settings)

    shared = _Shared()
    # 모델별 속도 제한기 — 한도(RPM)는 모델마다 따로다
    limiters = {m: RateLimiter(settings.article_target_rpm) for m in models}
    workers = max(1, settings.article_stream_workers)

    if len(models) == 1 and workers == 1:
        _process_stream(
            conn, provider, models[0], settings, usage, shared, limiters[models[0]]
        )
        return

    print(
        f"[analyze] {len(models)}모델 × 워커 {workers} "
        f"(모델당 목표 {settings.article_target_rpm:.0f} RPM): {', '.join(models)}"
    )
    threads = [
        threading.Thread(
            target=_stream_worker,
            args=(model, settings, usage, shared, limiters[model]),
            name=f"{model[-10:]}-{i}",
            daemon=True,
        )
        for model in models
        for i in range(workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def _stream_worker(
    model: str,
    settings: Settings,
    usage: UsageRecorder,
    shared: _Shared,
    limiter: RateLimiter,
) -> None:
    """워커 1개 — psycopg 연결은 스레드마다 별도여야 한다."""
    conn = None
    try:
        conn = connect(settings.supabase_db_url)
        provider = GeminiProvider(settings.gemini_api_key)
        _process_stream(conn, provider, model, settings, usage, shared, limiter)
    except Exception as e:  # noqa: BLE001 — 한 워커 실패가 배치를 죽이지 않게
        print(f"[analyze] 워커 {model} 실패: {e}", file=sys.stderr)
    finally:
        if conn is not None:
            conn.close()


def _process_stream(
    conn,
    provider: AIProvider,
    model: str,
    settings: Settings,
    usage: UsageRecorder,
    shared: _Shared,
    limiter: RateLimiter,
) -> None:
    worker_id = f"{socket.gethostname()}-{usage.run_id or 'local'}-{model[-12:]}"
    consecutive_rate_limits = 0
    # 신선도 우선 정렬(0021)의 짝 — 배치의 일부 슬롯은 오래 기다린 것에 준다.
    # 없으면 유입 > 처리인 동안 옛 job이 '나중에'가 아니라 '영원히' 안 뽑힌다.
    starve_every = max(
        2, settings.article_batch_max_count
        // max(1, starvation_slots(settings.article_batch_max_count))
    )

    while True:
        # 종료 조건 (설계 §9.5)
        if usage.elapsed_seconds() >= settings.article_batch_max_seconds:
            print(f"[analyze:{model}] 내부 시간 한도 도달 — 종료")
            break
        if consecutive_rate_limits >= MAX_CONSECUTIVE_RATE_LIMITS:
            # 429가 연달아 나면 남은 예산을 429로 태우지 않고 즉시 접는다.
            # (간격을 좁힌 뒤 사고가 나면 폭발 반경이 커지므로 필요한 차단기)
            print(f"[analyze:{model}] 연속 429 — 조기 종료", file=sys.stderr)
            break
        slot = shared.take_slot(settings.article_batch_max_count)
        if slot is None:
            print(f"[analyze:{model}] 배치 최대 건수 도달 — 종료")
            break
        processed = slot - 1  # 기아 슬롯 계산용 (0-based)
        # 쿼터일은 배치 도중에도 넘어간다(UTC-8 자정 = KST 17시). 시작할 때
        # 한 번 잡아 두면 리셋 뒤의 호출까지 어제 날짜로 세어, 새 쿼터일의
        # 우리 카운터가 구글 집계보다 작아진다 — 소프트리밋이 위험한 쪽으로
        # 틀린다. 막차 소진(예산 540초)이 리셋 직전 창에서 도는 만큼 실제로
        # 걸치는 경로다 (2026-09-08).
        quota_date = quota_date_pt()
        calls_today = todays_call_count(conn, model, quota_date)
        # 야간에는 오전 몫(조간 뉴스 분석분)을 남겨둔다 — 독자의 하루(아침
        # 열람)와 쿼터의 하루(PT 자정=KST 오후 4~5시 시작)의 어긋남 보정
        kst_hour = datetime.now(ZoneInfo("Asia/Seoul")).hour
        effective_limit = nightly_soft_limit(
            settings.article_daily_soft_limit,
            settings.morning_reserve_calls,
            kst_hour,
        )
        if calls_today >= effective_limit - settings.brief_daily_reserve:
            shared.give_back()
            if effective_limit < settings.article_daily_soft_limit:
                print(f"[analyze:{model}] 야간 상한 도달(오전 예약분 보존) — 종료")
            else:
                print(f"[analyze:{model}] 일일 내부 목표 도달 — 종료")
            break

        # starve_every 건마다 한 번은 '오래 기다린 것'을 집어 기아를 막는다
        prefer_fresh = (processed + 1) % starve_every != 0
        job = acquire_next_job(conn, worker_id, prefer_fresh=prefer_fresh)
        if job is None and not prefer_fresh:
            job = acquire_next_job(conn, worker_id, prefer_fresh=True)
        if job is None:
            shared.give_back()
            print(f"[analyze:{model}] 대기 작업 없음 — 종료")
            break

        job_id = str(job["id"])
        cluster_id = str(job["cluster_id"])
        attempt = job["attempt_count"]

        prompt = build_article_prompt(conn, cluster_id, settings)
        if prompt is None:
            fail_job(conn, job_id, attempt, "NO_CONTENT", "분석할 본문이 없음", retryable=False)
            shared.give_back()
            continue

        # 분당 한도를 지키는 선에서 가능한 한 빨리 — 고정 sleep 대신
        # 직전 호출 이후 경과분을 뺀 만큼만 기다린다
        limiter.acquire()
        analysis_start = time.monotonic()
        try:
            result = provider.analyze_article(prompt, model)
            _bump(usage, "api_calls", shared)
            _record_call(conn, model, job_id, "OK")
        except RateLimitError:
            _bump(usage, "api_calls", shared)
            _record_call(conn, model, job_id, "RATE_LIMITED", error_code="429")
            retry_job_after_rate_limit(conn, job_id, attempt)
            consecutive_rate_limits += 1
            shared.give_back()
            print(f"[analyze:{model}] 429 — 지연 재시도 예약")
            time.sleep(settings.article_request_interval_seconds)
            continue
        except ProviderServerError as e:
            _record_call(conn, model, job_id, "ERROR", error_code="5xx")
            fail_job(conn, job_id, attempt, "SERVER_ERROR", str(e))
            shared.give_back()
            time.sleep(settings.article_request_interval_seconds)
            continue
        except InvalidResponseError as e:
            # 비정상 JSON은 호출 단계에서 발생한다 — 작업만 재시도 처리하고
            # 배치는 계속한다 (외부 리뷰 P0-4)
            _bump(usage, "api_calls", shared)
            _record_call(conn, model, job_id, "OK")
            fail_job(conn, job_id, attempt, "INVALID_OUTPUT", str(e))
            shared.give_back()
            time.sleep(settings.article_request_interval_seconds)
            continue
        except Exception as e:  # noqa: BLE001 — 어떤 오류든 잠금을 반드시 정리
            _record_call(conn, model, job_id, "ERROR", error_code="UNKNOWN")
            fail_job(conn, job_id, attempt, "PROVIDER_UNKNOWN", str(e))
            shared.give_back()
            time.sleep(settings.article_request_interval_seconds)
            continue

        try:
            analysis, validation_status = validate_analysis(result.data)
        except SchemaValidationError as e:
            # 잘못된 출력 자동 재시도, 반복 실패 시 메타데이터만 보존 (FR-007)
            fail_job(conn, job_id, attempt, "INVALID_OUTPUT", str(e))
            shared.give_back()
            time.sleep(settings.article_request_interval_seconds)
            continue

        try:
            # 게시는 스트림 간 직렬화 — 같은 사건 기사가 동시에 올라가면
            # 서로를 못 보고 중복 게시된다 (_Shared.publish_lock 주석 참고)
            with shared.publish_lock:
                analysis_id = save_analysis(
                    conn, cluster_id, analysis, validation_status,
                    result.model_name, result.raw_text,
                    result.input_tokens, result.output_tokens,
                )
                publish_analysis(conn, cluster_id, analysis_id, analysis)
        except Exception as e:  # noqa: BLE001 — 저장 실패도 잠금을 정리
            conn.rollback()
            fail_job(conn, job_id, attempt, "SAVE_ERROR", str(e))
            shared.give_back()
            time.sleep(settings.article_request_interval_seconds)
            continue
        complete_job(conn, job_id)
        consecutive_rate_limits = 0
        with shared.counter_lock:
            usage.counts["analyzed"] = shared.processed
            usage.stage_seconds["analysis"] = usage.stage_seconds.get(
                "analysis", 0
            ) + int(time.monotonic() - analysis_start)
        # 속도 제한은 다음 루프의 limiter.acquire()가 담당한다


def maybe_enable_last_call_drain(
    settings: Settings, pending: int, batch_capacity: int | None = None
) -> bool:
    """쿼터 리셋 전 '막차' 소진 (사용자 지시 2026-08-09).

    쿼터는 리셋되면 증발하므로, 리셋이 임박했고 태울 백로그가 있으면
    건수 캡을 풀고 시간 예산을 늘린다.

    2026-08-11 수정: 기존 조건은 'KST 15시에 시작'이었는데 크론이 실제로
    +13~95분 지연돼 16시 이후 시작이 잦았고, 그 결과 한 번도 발동하지
    못했다(실측). 시각 대신 '리셋까지 남은 시간'으로 판정한다.
    일일 소프트리밋 검사는 그대로 유효하다 (쿼터 초과 방지).

    2026-09-08 수정: 예전에는 운영자가 건수를 지정하면(ARTICLE_BATCH_MAX_COUNT)
    막차 판정 자체를 건너뛰었다. 그래서 KST 14~17시에 건수를 적으면 시간
    예산이 540 → 420초로 **줄어 오히려 적게** 처리됐다 — 화면에서 그 숫자는
    '최대 몇 건'으로만 읽히는데 실제로는 시간까지 깎은 셈이다. 이제 판정은
    건수 지정과 무관하게 하고, 지정한 캡은 999로 덮지 않고 그대로 존중한다.

    batch_capacity: '태울 백로그가 있는가'를 재는 잣대 — 이번 배치가 시간
        예산으로 처리할 수 있는 건수(runaway_cap_floor)를 넣는다. 운영자가
        고른 숫자를 여기 쓰면 큰 값을 적을수록 막차가 안 걸리는 모순이 생긴다.
        생략하면 예전처럼 현재 건수 캡을 쓴다.
    """
    manual_cap = bool(os.environ.get("ARTICLE_BATCH_MAX_COUNT"))
    if batch_capacity is None:
        batch_capacity = settings.article_batch_max_count
    if not should_drain(hours_until_quota_reset(), pending, batch_capacity):
        return False
    if not manual_cap:
        settings.article_batch_max_count = 999
    settings.article_batch_max_seconds = int(
        os.environ.get("ARTICLE_DRAIN_MAX_SECONDS") or "540"
    )
    return True


def main() -> int:
    settings = Settings.from_env()
    settings.require("supabase_db_url", "gemini_api_key")

    conn = connect(settings.supabase_db_url)
    settings.apply_app_settings(load_app_settings(conn))

    # 강제 종료로 RUNNING에 굳은 이전 실행 기록 정리
    stale = close_stale_runs(conn)
    if stale:
        print(f"[analyze] 중단된 실행 기록 {stale}건 정리")

    # 시간대별 예산 재배분 (사용자 지시): 독자는 아침에만 보므로 조간
    # 배치에 시간을 몰아주고 낮·저녁은 짧게. 하루 총량은 그대로.
    if not os.environ.get("ARTICLE_BATCH_MAX_SECONDS"):
        kst_hour_now = datetime.now(ZoneInfo("Asia/Seoul")).hour
        settings.article_batch_max_seconds = batch_budget_seconds(
            kst_hour_now, settings.article_batch_max_seconds
        )

    # 이 시간 예산으로 처리할 수 있는 최대 건수. 건수 캡이 이보다 낮으면
    # 캡이 먼저 걸려 '무엇이 배치를 끝냈는지' 알 수 없게 된다 (09-08 실측).
    manual_cap = bool(os.environ.get("ARTICLE_BATCH_MAX_COUNT"))
    cap_floor = runaway_cap_floor(
        settings.article_batch_max_seconds,
        len(resolved_models(settings)),
        settings.article_target_rpm,
        settings.article_stream_workers,
    )
    # 수동 실행이 건수를 지정했으면 그 숫자를 그대로 상한으로 쓴다.
    if not manual_cap and settings.article_batch_max_count < cap_floor:
        print(
            f"[analyze] 건수 캡 {settings.article_batch_max_count} → {cap_floor} "
            f"(시간 예산 {settings.article_batch_max_seconds}초가 먼저 걸리도록)"
        )
        settings.article_batch_max_count = cap_floor

    pending_now, _ = count_pending(conn)
    # 막차 판정의 잣대는 '이번 배치가 처리할 수 있는 양'(cap_floor)이다 —
    # 운영자가 고른 건수를 쓰면 그 숫자가 시간 예산까지 좌우한다 (결함 D).
    drain = maybe_enable_last_call_drain(settings, pending_now, cap_floor)
    if drain:
        print(f"[analyze] 막차 소진 모드 — 대기 {pending_now}건, "
              f"시간 예산 {settings.article_batch_max_seconds}초")

    # 운영 화면 표에서 "어떤 모델이 최대 몇 건"인지 보이게 기록 (0017)
    streams = resolved_models(settings)
    label = " + ".join(streams) if len(streams) > 1 else streams[0]
    if drain and not manual_cap:
        note = f"{label} · 막차 소진"
    elif drain:
        note = f"{label} · 막차 소진 · 최대 {settings.article_batch_max_count}건"
    else:
        note = f"{label} · 최대 {settings.article_batch_max_count}건"
    usage = UsageRecorder(conn, "analyze", note=note)

    status = "SUCCESS"
    try:
        recovered = recover_stale_locks(conn)
        if recovered:
            print(f"[analyze] 오래된 잠금 {recovered}건 복구")

        provider = GeminiProvider(settings.gemini_api_key)
        process_queue(conn, provider, settings, usage)

        # 배치 후 중복 스윕 — 같은 배치·병행 실행이 나란히 게시한 동일 사건
        # 기사를 사후 정리한다 (스윕 실패가 배치를 실패시키지는 않음)
        try:
            from .dedup_sweep import run_sweep

            swept = run_sweep(conn)
            if swept:
                print(f"[analyze] 중복 스윕: {swept}건 정리")
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            print(f"[analyze] 중복 스윕 실패(무시): {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        status = "FAILURE"
        print(f"[analyze] 배치 실패: {e}", file=sys.stderr)
    finally:
        pending, oldest = count_pending(conn)
        usage.finish(status, remaining_pending=pending, oldest_pending_minutes=oldest)
        conn.close()
    return 0 if status == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
