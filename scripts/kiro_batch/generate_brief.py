"""주간 브리프 생성 배치 (FR-012 개정 — WEEKLY 전환, 외부 리뷰 제안).

실행: python -m kiro_batch.generate_brief
환경변수 BRIEF_FORCE=true 로 기간 판정을 무시하고 강제 생성한다.
환경변수 BRIEF_WEEK_START=YYYY-MM-DD 로 특정 주차(그 날짜가 속한 월~일)를
지정한다 — 기발행이어도 새 버전을 만든다(force 포함). 월요일이 아니면 그 주
월요일로 보정하고, 아직 끝나지 않은 주차·미래 주차는 거부한다
(관리 화면 '이 주차 다시 생성' / '특정 주차 생성', 2026-09-08).

- 대상 기간: 월요일 00:00 ~ 일요일 23:59:59 (KST 고정 주차), 다음 월요일 발행
- 기간 판정: source_published_at 우선, null이면 published_at
- 후보 선별 20~35건 (중요도·KIRO 직접·1차 자료 우선, 분야 대표성)
- 기존 격주(BIWEEKLY) 브리프는 아카이브로 보존 (0013)
- 3개 섹션 분할 생성 + 코드 조립, 최대 6회 호출, 버전 보존 유지
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError

from .config import Settings
from .db import connect, load_app_settings
from .gemini_provider import (
    AIProvider,
    GeminiProvider,
    InvalidResponseError,
    ProviderServerError,
    RateLimitError,
)
from .queue import record_gemini_call
from .quota import quota_date_pt
from .usage import UsageRecorder

BRIEF_PROMPT_VERSION = "brief_weekly_v2"  # v2: section3에 KIRO 공개 컨텍스트 주입

# 이번 실행에서 허용하는 AI 호출 횟수 — 폭주 방지용 안전장치일 뿐,
# 평상시 필요량(섹션 3개 × 모델 2개)을 절대 깎으면 안 된다.
#
# 2026-09-08 정정 (5 → 6). 5로 두는 동안 마지막 섹션은 예비 모델을 아예 못 썼다:
# 기본 모델이 503이면 섹션1(1콜 실패)→예비(2콜 성공), 섹션2(3콜 실패)→예비(4콜 성공),
# 섹션3(5콜 실패)에서 상한에 걸려 예비 모델 시도가 통째로 막힌다. 그래서
# 08-17·08-24·09-01 브리프 세 건이 모두 '전주 대비 변화·KIRO 시사점·다음 주 추적
# 항목'(섹션3)을 비운 채 발행됐고, 화면에는 진짜 원인(503)이 아니라 '호출 예산
# 소진'만 남았다 (gemini_calls 실측).
MAX_BRIEF_CALLS = 3 * 2  # 섹션 3개 × (기본 모델 + 예비 모델)

# 실패 사유 문구 — 관리 화면(admin/briefs)의 변환 표와 짝이 맞는다.
# 운영자는 비개발자이므로 짧은 한국어 한 마디만 DB에 남기고, 예외 원문·모델명·
# HTTP 코드는 stderr(Actions 로그)에만 쓴다.
REASON_RATE_LIMIT = "AI 하루 호출 한도 초과"
REASON_SERVER_BUSY = "AI 서버 혼잡(일시적)"
REASON_BAD_FORMAT = "AI 응답 형식 오류"
# '예산 소진'은 운영자에게 'Gemini 한도를 다 썼다'로 읽힌다 — 실제 뜻은
# 이번 실행에서 정한 시도 횟수를 다 썼다는 것뿐이다 (2026-09-08 피드백 7).
REASON_RETRY_LIMIT = "AI 재시도 횟수 초과"
REASON_UNKNOWN = "AI 생성 실패"

# 섹션마다 사유가 다를 때 앞에 놓을 순서 — 운영자가 먼저 알아야 할 원인이 위다.
# '재시도 횟수 초과'는 결과이지 원인이 아니므로 진짜 원인 뒤에 둔다.
REASON_PRIORITY = (
    REASON_RATE_LIMIT,
    REASON_SERVER_BUSY,
    REASON_BAD_FORMAT,
    REASON_RETRY_LIMIT,
    REASON_UNKNOWN,
)


def merge_failure_reasons(reasons) -> list[str]:
    """섹션별 실패 사유를 중복 없이 중요한 순서로 정리한다 (순수 함수 — 테스트 대상).

    화면은 맨 앞 하나만 보여 주므로 순서가 곧 '가장 중요한 원인'이다.
    """
    unique: list[str] = []
    for reason in reasons:
        text = (reason or "").strip()
        if text and text not in unique:
            unique.append(text)

    def rank(reason: str) -> int:
        try:
            return REASON_PRIORITY.index(reason)
        except ValueError:
            return len(REASON_PRIORITY)  # 모르는 문구는 뒤로 (정렬은 안정적)

    return sorted(unique, key=rank)

# 후보 선별 상한 (분야 대표성 + 총량 제한)
CANDIDATE_CAPS = {"정책": 10, "산업": 12, "기술": 12}
CANDIDATE_TOTAL_MAX = 35

_KST = ZoneInfo("Asia/Seoul")
_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


def previous_completed_week(kst_today: date) -> tuple[date, date]:
    """kst_today 기준 직전 완결 주차(월~일)를 반환한다 (순수 함수 — 테스트 대상).

    예: 2026-08-10(월) → (2026-08-03, 2026-08-09)
        2026-08-08(토) → (2026-07-27, 2026-08-02)  # 이번 주는 아직 미완결
    """
    monday_this_week = kst_today - timedelta(days=kst_today.weekday())
    return monday_this_week - timedelta(days=7), monday_this_week - timedelta(days=1)


class WeekStartError(ValueError):
    """BRIEF_WEEK_START 거부 사유 — 운영자가 읽을 한국어 메시지를 담는다."""


_WEEKDAY_KO = "월화수목금토일"


def resolve_week_start(raw: str, kst_today: date) -> tuple[date, date, str | None]:
    """BRIEF_WEEK_START(YYYY-MM-DD)를 대상 주차(월~일)로 해석한다 (순수 함수 — 테스트 대상).

    - 월요일이 아니면 그 주의 월요일로 보정하고, 보정 사실을 세 번째 값(note)으로 돌려준다
    - 아직 끝나지 않은 주차(일요일이 오늘이거나 그 뒤)와 미래 주차는 WeekStartError
      → 정기 배치와 같은 기준: 다음 월요일(kst_today > period_end)부터 생성 가능
    - 빈 값·형식 오류·존재하지 않는 날짜도 WeekStartError

    예: ("2026-08-05"(수), today=2026-09-08) → (2026-08-03, 2026-08-09, "…보정")
    """
    text = (raw or "").strip()
    try:
        # fromisoformat은 3.11부터 '20260831' 같은 축약형도 받으므로 형식을 먼저 고정한다
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            raise ValueError(text)
        requested = date.fromisoformat(text)
    except ValueError:
        raise WeekStartError(
            f"week_start 형식 오류: '{text}' — YYYY-MM-DD 형식의 날짜(월요일)여야 합니다"
        ) from None

    period_start = requested - timedelta(days=requested.weekday())
    period_end = period_start + timedelta(days=6)

    note = None
    if requested != period_start:
        note = (
            f"{requested}({_WEEKDAY_KO[requested.weekday()]})은 월요일이 아니어서 "
            f"그 주 월요일 {period_start}로 보정"
        )

    if period_end >= kst_today:
        raise WeekStartError(
            f"{period_start}~{period_end} 주차는 아직 끝나지 않았거나 미래 주차입니다 "
            f"(오늘 {kst_today}). 일요일이 지난 뒤(월요일부터) 생성할 수 있습니다"
        )
    return period_start, period_end, note


def kst_bounds(period_start: date, period_end: date) -> tuple[datetime, datetime]:
    """주차 경계를 KST 자정 기준 timestamptz 범위로 변환한다."""
    start = datetime.combine(period_start, time.min, tzinfo=_KST)
    end = datetime.combine(period_end + timedelta(days=1), time.min, tzinfo=_KST)
    return start, end


class Section1(BaseModel):
    one_page_summary: str
    policy_headline: str = ""
    key_changes: list[str] = Field(min_length=1)
    policy_domestic: str = ""
    policy_us: str = ""
    policy_china: str = ""
    policy_japan: str = ""
    policy_europe_etc: str = ""


class Section2(BaseModel):
    industry_headline: str = ""
    industry_overseas: str = ""
    industry_domestic: str = ""
    tech_headline: str = ""
    tech_trends: str = ""


class Section3(BaseModel):
    changes_from_previous: str = ""
    kiro_implications: str = ""
    tracking_items: list[str] = Field(default_factory=list)


def determine_period(conn, kst_today: date) -> tuple[date, date] | None:
    """이번에 발행할 주간 기간(KST 월~일). 아직 아니면 None.

    - 직전 완결 주차에 대해 WEEKLY 브리프가 이미 발행됐으면 건너뜀
    - 격주 아카이브가 이미 완전히 덮은 주차도 건너뜀 (전환 경계 중복 방지)
    """
    period_start, period_end = previous_completed_week(kst_today)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM brief_periods pr
            JOIN briefs b ON b.brief_period_id = pr.id
            WHERE pr.period_start = %s AND pr.period_end = %s
              AND b.status = 'PUBLISHED'
            LIMIT 1
            """,
            (period_start, period_end),
        )
        if cur.fetchone():
            return None

        cur.execute(
            "SELECT max(period_end) AS last_end FROM brief_periods WHERE status = 'PUBLISHED'"
        )
        last_end = cur.fetchone()["last_end"]

    # 기존(격주 포함) 발행분이 이 주차 전체를 이미 덮었으면 생성하지 않음
    if last_end is not None and period_end <= last_end:
        return None
    return period_start, period_end


def select_brief_targets(conn, period_start: date, period_end: date) -> list[dict]:
    """주간 후보 선별 (외부 리뷰 지시 3항).

    - 기간 판정: source_published_at 우선, null이면 published_at (KST 경계)
    - 우선순위: 중요도 → KIRO 직접 관련 → 근거 수준 → 정부·공공 1차 자료 → 최신
    - 분야 상한(정책 10 / 산업 12 / 기술 12) + 총 35건
    """
    start_ts, end_ts = kst_bounds(period_start, period_end)
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH ranked AS (
              SELECT p.id, p.title, p.category, p.region, p.robot_field,
                     p.importance, p.evidence_level, p.representative_url,
                     p.representative_source_name, p.current_analysis_id,
                     a.one_line_summary, a.verified_facts, a.kiro_implication,
                     a.kiro_relevance, a.kiro_relevance_axes, a.kiro_watchpoints,
                     row_number() OVER (
                       PARTITION BY p.category
                       ORDER BY
                         CASE p.importance WHEN '높음' THEN 0 WHEN '보통' THEN 1 ELSE 2 END,
                         CASE a.kiro_relevance WHEN '직접' THEN 0 WHEN '간접' THEN 1 ELSE 2 END,
                         CASE p.evidence_level WHEN '강함' THEN 0 WHEN '보통' THEN 1 ELSE 2 END,
                         CASE WHEN s.source_type IN ('정부·공공기관', '정책·사업 공고')
                              THEN 0 ELSE 1 END,
                         coalesce(p.source_published_at, p.published_at) DESC
                     ) AS rank_in_category
              FROM published_items p
              JOIN analyses a ON a.id = p.current_analysis_id
              JOIN content_clusters c ON c.id = p.cluster_id
              LEFT JOIN raw_items ri ON ri.id = c.representative_raw_item_id
              LEFT JOIN sources s ON s.id = ri.source_id
              WHERE p.is_visible = true
                AND coalesce(p.source_published_at, p.published_at) >= %s
                AND coalesce(p.source_published_at, p.published_at) < %s
            )
            SELECT * FROM ranked
            WHERE (category = '정책' AND rank_in_category <= %s)
               OR (category = '산업' AND rank_in_category <= %s)
               OR (category = '기술' AND rank_in_category <= %s)
            ORDER BY category, rank_in_category
            LIMIT %s
            """,
            (
                start_ts, end_ts,
                CANDIDATE_CAPS["정책"], CANDIDATE_CAPS["산업"],
                CANDIDATE_CAPS["기술"], CANDIDATE_TOTAL_MAX,
            ),
        )
        return cur.fetchall()


def _as_list(value) -> list:
    if isinstance(value, str):
        return json.loads(value or "[]")
    return list(value or [])


def format_items(targets: list[dict]) -> str:
    """동향 목록을 [번호] 형식 프롬프트 입력으로 구성 (전문 미포함).

    v1.1: KIRO 관련성·업무축·워치포인트를 포함해 브리프의
    KIRO 시사점 종합과 추적 항목 생성의 근거로 쓴다.
    """
    lines = []
    for i, t in enumerate(targets, start=1):
        facts = _as_list(t["verified_facts"])
        fact_lines = "\n".join(f"    - {f}" for f in facts[:4])
        axes = _as_list(t.get("kiro_relevance_axes"))
        watchpoints = _as_list(t.get("kiro_watchpoints"))

        extra = []
        if t.get("kiro_relevance"):
            axes_text = f" [{', '.join(axes)}]" if axes else ""
            extra.append(f"    KIRO 관련성: {t['kiro_relevance']}{axes_text}")
        if t.get("kiro_implication"):
            extra.append(f"    KIRO 시사점: {t['kiro_implication'][:200]}")
        for w in watchpoints[:3]:
            extra.append(f"    추적: {w}")

        lines.append(
            f"[{i}] ({t['category']}·{t['region']}·{t['robot_field']}"
            f"·중요도 {t['importance']}·근거 {t['evidence_level']}) {t['title']}\n"
            f"    요약: {t['one_line_summary'] or ''}\n"
            f"{fact_lines}"
            + ("\n" + "\n".join(extra) if extra else "")
        )
    return "\n".join(lines)


def generate_section(
    provider: AIProvider,
    conn,
    settings: Settings,
    prompt: str,
    schema: type[BaseModel],
    calls_used: list[int],
) -> tuple[BaseModel | None, str | None]:
    """섹션 생성: 기본 모델 1회 → 실패 시 예비 모델 1회 (시도 횟수 안에서).

    두 번째 반환값은 briefs.failure_reason에 저장돼 운영 화면에 그대로 보인다.
    운영자는 비개발자이므로 **짧은 한국어 한 마디**만 돌려주고, 예외 원문·모델명은
    stderr(Actions 로그)에만 남긴다 (2026-09-08, 사용자 요구 6-1).
    여기서 쓰는 문구는 admin/briefs 화면의 변환 표와 짝이 맞는다.

    MAX_BRIEF_CALLS에 걸려 예비 모델을 못 써도 **직전 시도가 실패한 진짜 이유**
    (503 등)를 돌려준다 — 상한은 결과이지 원인이 아니다. 실패 이유가 하나도 없이
    상한에만 걸린 경우에만 '재시도 횟수 초과'를 남긴다 (2026-09-08 피드백 7:
    운영자가 '예산 소진'을 Gemini 한도를 다 쓴 것으로 읽었다).
    """
    last_reason: str | None = None
    last_detail = ""
    models = (settings.brief_model, settings.brief_fallback_model)
    for attempt, model_name in enumerate(models, start=1):
        if calls_used[0] >= MAX_BRIEF_CALLS:
            print(
                f"[brief] 섹션 생성 중단: 이번 실행 시도 횟수 {MAX_BRIEF_CALLS}회를 "
                f"다 씀 (모델 {model_name} 시도 전) — 직전 실패: {last_detail or '없음'}",
                file=sys.stderr,
            )
            return None, last_reason or REASON_RETRY_LIMIT
        calls_used[0] += 1
        quota_date = quota_date_pt()
        try:
            result = provider.generate_brief_section(prompt, model_name)
            record_gemini_call(conn, model_name, quota_date, "OK")
            section = schema.model_validate(result.data)
            return section, None
        except RateLimitError as e:
            record_gemini_call(conn, model_name, quota_date, "RATE_LIMITED", "429")
            last_reason = REASON_RATE_LIMIT
            last_detail = f"{model_name}: 429 {e}"
        except ProviderServerError as e:
            record_gemini_call(conn, model_name, quota_date, "ERROR", "5xx")
            last_reason = REASON_SERVER_BUSY
            last_detail = f"{model_name}: 5xx {e}"
        except (InvalidResponseError, ValidationError) as e:
            record_gemini_call(conn, model_name, quota_date, "OK")
            last_reason = REASON_BAD_FORMAT
            last_detail = f"{model_name}: 출력 형식 오류 {e}"
        # DB에는 짧은 한국어만 남으므로 예외 원문(모델·HTTP 코드·응답 본문)은
        # 반드시 여기에 남긴다 — 나중에 원인을 확인할 유일한 기록이다.
        next_step = (
            "예비 모델로 재시도" if attempt < len(models) else "이 섹션은 포기"
        )
        print(
            f"[brief] 섹션 생성 실패({last_reason}) — {last_detail} → {next_step}",
            file=sys.stderr,
        )
    return None, last_reason or REASON_UNKNOWN


FAILED_TEXT = "⚠️ 이 섹션은 자동 생성에 실패했습니다. 운영자가 재생성할 수 있습니다."


def assemble_markdown(
    title: str,
    period_start: date,
    period_end: date,
    s1: Section1 | None,
    s2: Section2 | None,
    s3: Section3 | None,
    targets: list[dict],
) -> str:
    """세 섹션을 기존 KIRO 보고서 골격으로 조립 (tasks §13.4)."""

    def sec(text: str | None) -> str:
        return text.strip() if text and text.strip() else "이번 기간 특기할 동향 없음"

    lines = [
        f"# {title}",
        f"\n대상 기간: {period_start} ~ {period_end}\n",
        "## 1. 한 페이지 요약",
        sec(s1.one_page_summary) if s1 else FAILED_TEXT,
        "\n### 이번 기간 핵심 변화",
    ]
    if s1:
        lines += [c for c in s1.key_changes]
    else:
        lines.append(FAILED_TEXT)

    lines += ["\n## 2. 정책 동향"]
    if s1:
        if s1.policy_headline:
            lines.append(f"**{s1.policy_headline}**\n")
        lines += [
            "### 국내", sec(s1.policy_domestic),
            "### 미국", sec(s1.policy_us),
            "### 중국", sec(s1.policy_china),
            "### 일본", sec(s1.policy_japan),
            "### 유럽·기타", sec(s1.policy_europe_etc),
        ]
    else:
        lines.append(FAILED_TEXT)

    lines += ["\n## 3. 산업 동향"]
    if s2:
        if s2.industry_headline:
            lines.append(f"**{s2.industry_headline}**\n")
        lines += [
            "### 해외", sec(s2.industry_overseas),
            "### 국내", sec(s2.industry_domestic),
        ]
    else:
        lines.append(FAILED_TEXT)

    lines += ["\n## 4. 기술 동향"]
    if s2:
        if s2.tech_headline:
            lines.append(f"**{s2.tech_headline}**\n")
        lines.append(sec(s2.tech_trends))
    else:
        lines.append(FAILED_TEXT)

    lines += [
        "\n## 5. 전주 대비 변화",
        sec(s3.changes_from_previous) if s3 else FAILED_TEXT,
        "\n## 6. KIRO 시사점",
        sec(s3.kiro_implications) if s3 else FAILED_TEXT,
        "\n## 7. 다음 주 추적 항목",
    ]
    if s3 and s3.tracking_items:
        lines += [f"- {t}" for t in s3.tracking_items]
    else:
        lines.append(FAILED_TEXT if not s3 else "이번 기간 특기할 항목 없음")

    # 부록: 동향별 상세 — AI 호출 없이 데이터에서 조립 (사용자 피드백 5)
    lines += ["\n## 부록. 동향별 상세"]
    current_category = None
    for i, t in enumerate(targets, start=1):
        if t["category"] != current_category:
            current_category = t["category"]
            lines.append(f"\n### {current_category}")
        facts = t["verified_facts"]
        if isinstance(facts, str):
            facts = json.loads(facts or "[]")
        lines.append(f"\n□ [{i}] {t['title']}")
        if t["one_line_summary"]:
            lines.append(f" ㅇ 요약: {t['one_line_summary']}")
        for f in facts[:3]:
            lines.append(f" ㅇ {f}")
        lines.append(
            f" ※ 출처: {t['representative_source_name'] or ''} {t['representative_url']}"
        )

    lines += ["\n## 근거자료"]
    for i, t in enumerate(targets, start=1):
        lines.append(
            f"- [{i}] {t['title']} — {t['representative_source_name'] or ''} "
            f"({t['representative_url']})"
        )
    return "\n".join(lines)


def get_or_create_period(conn, period_start: date, period_end: date) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO brief_periods (period_start, period_end, status, cadence)
            VALUES (%s, %s, 'GENERATING', 'WEEKLY')
            ON CONFLICT (period_start, period_end)
            DO UPDATE SET status = 'GENERATING', updated_at = now()
            RETURNING id
            """,
            (period_start, period_end),
        )
        period_id = str(cur.fetchone()["id"])
    conn.commit()
    return period_id


def save_brief(
    conn,
    period_id: str,
    title: str,
    status: str,
    s1: Section1 | None,
    s2: Section2 | None,
    s3: Section3 | None,
    markdown: str,
    model_name: str,
    targets: list[dict],
    failure_reason: str | None,
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT coalesce(max(version), 0) + 1 AS v FROM briefs WHERE brief_period_id = %s",
            (period_id,),
        )
        version = cur.fetchone()["v"]
        # 기존 버전은 보존하되 현재 표시만 해제 (tasks §13.5).
        # 단, 이번 생성이 전부 실패(FAILED)했으면 현재본을 바꾸지 않는다 —
        # 관리 화면 재생성이 실패했을 때 이미 공개된 발행본이 목록에서
        # 사라지면 안 된다 (2026-09-08). 실패본은 is_current=false로 기록만 남긴다.
        make_current = status == "PUBLISHED"
        if make_current:
            cur.execute(
                "UPDATE briefs SET is_current = false WHERE brief_period_id = %s",
                (period_id,),
            )
        cur.execute(
            """
            INSERT INTO briefs (
              brief_period_id, version, title, status,
              section_summary_policy, section_industry_tech, section_outlook,
              assembled_markdown, model_name, prompt_version,
              generated_at, published_at, is_current, failure_reason
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(),
                    CASE WHEN %s = 'PUBLISHED' THEN now() END, %s, %s)
            RETURNING id
            """,
            (
                period_id, version, title, status,
                json.dumps(s1.model_dump(), ensure_ascii=False) if s1 else None,
                json.dumps(s2.model_dump(), ensure_ascii=False) if s2 else None,
                json.dumps(s3.model_dump(), ensure_ascii=False) if s3 else None,
                markdown, model_name, BRIEF_PROMPT_VERSION,
                status, make_current, failure_reason,
            ),
        )
        brief_id = str(cur.fetchone()["id"])

        for i, t in enumerate(targets, start=1):
            facts = t["verified_facts"]
            if isinstance(facts, str):
                facts = json.loads(facts or "[]")
            # 발행 당시 상태를 스냅샷으로 고정 — 이후 재분석이
            # 과거 브리프를 바꾸지 못한다 (외부 리뷰 P1-6, FR-013)
            cur.execute(
                """
                INSERT INTO brief_items
                  (brief_id, published_item_id, section, display_order,
                   is_low_confidence, analysis_id_snapshot, title_snapshot,
                   summary_snapshot, facts_snapshot,
                   source_name_snapshot, source_url_snapshot)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    brief_id, t["id"], t["category"], i,
                    t["evidence_level"] == "약함",
                    t["current_analysis_id"], t["title"],
                    t["one_line_summary"],
                    json.dumps(facts, ensure_ascii=False),
                    t["representative_source_name"], t["representative_url"],
                ),
            )
        # 기간 상태: 이 기간에 발행본이 하나라도 있으면(방금 저장분 포함) PUBLISHED —
        # 재생성 실패가 기존 발행 기간을 FAILED로 되돌리지 않게 한다
        cur.execute(
            """
            UPDATE brief_periods
            SET status = CASE
                  WHEN EXISTS (SELECT 1 FROM briefs
                               WHERE brief_period_id = %s AND status = 'PUBLISHED')
                  THEN 'PUBLISHED' ELSE 'FAILED' END,
                updated_at = now()
            WHERE id = %s
            """,
            (period_id, period_id),
        )
    conn.commit()
    return brief_id


def load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def main() -> int:
    settings = Settings.from_env()
    settings.require("supabase_db_url", "gemini_api_key")

    conn = connect(settings.supabase_db_url)
    settings.apply_app_settings(load_app_settings(conn))
    usage = UsageRecorder(conn, "generate-brief")

    force = os.environ.get("BRIEF_FORCE", "").lower() == "true"
    week_start_raw = os.environ.get("BRIEF_WEEK_START", "").strip()
    status = "SUCCESS"
    try:
        kst_today = datetime.now(_KST).date()
        if week_start_raw:
            # 특정 주차 지정 (관리 화면 재생성): 기간 판정을 건너뛰고
            # 기발행이어도 새 버전을 만든다 — force 의미를 포함한다
            period_start, period_end, note = resolve_week_start(
                week_start_raw, kst_today
            )
            if note:
                print(f"[brief] {note}")
            print(
                f"[brief] 지정 주차 {period_start}~{period_end} 생성 "
                f"(기발행이어도 새 버전으로 저장)"
            )
        else:
            period = determine_period(conn, kst_today)
            if period is None and not force:
                print("[brief] 이번 주차는 발행 대상 아님(미완결 또는 기발행) — 종료")
                return 0
            if period is None:
                # 강제 생성: 직전 완결 주차를 판정 무시하고 재생성 (새 버전으로 저장)
                period = previous_completed_week(kst_today)
            period_start, period_end = period

        targets = select_brief_targets(conn, period_start, period_end)
        print(f"[brief] 기간 {period_start}~{period_end}, 대상 {len(targets)}건")
        if not targets:
            print("[brief] 대상 동향이 없어 브리프를 생성하지 않음")
            return 0

        period_id = get_or_create_period(conn, period_start, period_end)
        items_text = format_items(targets)
        provider = GeminiProvider(settings.gemini_api_key)
        calls_used = [0]

        def fill(template: str, extra: dict[str, str] | None = None) -> str:
            out = (
                template.replace("{{PERIOD_START}}", str(period_start))
                .replace("{{PERIOD_END}}", str(period_end))
                .replace("{{ITEMS}}", items_text)
            )
            for k, v in (extra or {}).items():
                out = out.replace(k, v)
            return out

        s1, err1 = generate_section(
            provider, conn, settings,
            fill(load_prompt("brief_section1_v2.txt")), Section1, calls_used,
        )
        s2, err2 = generate_section(
            provider, conn, settings,
            fill(load_prompt("brief_section2_v2.txt")), Section2, calls_used,
        )

        # 직전 주간 브리프의 요약·추적항목 (호출 3 입력 — 지속/신규 구분용)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT b.section_summary_policy->>'one_page_summary' AS prev,
                       b.section_outlook->'tracking_items' AS prev_watch
                FROM briefs b
                JOIN brief_periods pr ON pr.id = b.brief_period_id
                WHERE b.status = 'PUBLISHED' AND b.brief_period_id != %s
                  AND pr.cadence = 'WEEKLY'
                ORDER BY b.published_at DESC LIMIT 1
                """,
                (period_id,),
            )
            row = cur.fetchone()
            prev_summary = row["prev"] if row and row["prev"] else "(이전 주간 브리프 없음)"
            prev_watch = _as_list(row["prev_watch"]) if row else []
            prev_watch_text = (
                "\n".join(f"- {w}" for w in prev_watch) if prev_watch else "(없음)"
            )

        # 기사 분석과 동일한 KIRO 공개 컨텍스트 주입 (외부 리뷰 P1-4 —
        # 브리프 시사점이 구형 한 줄 소개 대신 브로슈어 기반 근거를 쓰게)
        kiro_context = (
            _PROMPTS_DIR / "context" / "kiro_public_context_v1.md"
        ).read_text(encoding="utf-8")

        s3, err3 = generate_section(
            provider, conn, settings,
            fill(
                load_prompt("brief_section3_v2.txt"),
                {
                    "{{KIRO_PUBLIC_CONTEXT}}": kiro_context,
                    "{{PREVIOUS_SUMMARY}}": prev_summary,
                    "{{PREVIOUS_WATCHPOINTS}}": prev_watch_text,
                },
            ),
            Section3, calls_used,
        )

        usage.counts["api_calls"] = calls_used[0]
        # 사유는 짧은 한국어라 세 섹션이 같은 이유로 실패하면 같은 문구가 세 번
        # 반복된다 — 중복을 지우고 중요한 순서로 정렬해 저장한다(화면은 맨 앞
        # 하나만 보여 준다). 구분자 '; '는 화면(admin/briefs)이 여러 사유를 갈라
        # 읽는 기준이므로 바꾸지 않는다.
        failed = merge_failure_reasons((err1, err2, err3))
        title = f"KIRO 로봇 주간 브리프 ({period_start} ~ {period_end})"
        markdown = assemble_markdown(
            title, period_start, period_end, s1, s2, s3, targets
        )

        # 일부 섹션 실패 시에도 나머지로 발행, 전체 실패 시 FAILED (FR-012)
        if s1 is None and s2 is None and s3 is None:
            brief_status = "FAILED"
            status = "FAILURE"
        else:
            brief_status = "PUBLISHED"

        brief_id = save_brief(
            conn, period_id, title, brief_status,
            s1, s2, s3, markdown, settings.brief_model, targets,
            "; ".join(failed) if failed else None,
        )
        failed_sections = sum(1 for e in (err1, err2, err3) if e)
        print(
            f"[brief] {brief_status}: {title} (id={brief_id}, "
            f"호출 {calls_used[0]}회, 실패 섹션 {failed_sections}개)"
        )
    except WeekStartError as e:
        # 지정 주차 거부 — DB 변경 없음. 실패로 끝내 Actions 실행 결과에 드러나게 한다
        status = "FAILURE"
        print(f"[brief] 거부: {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        status = "FAILURE"
        print(f"[brief] 실패: {e}", file=sys.stderr)
    finally:
        usage.finish(status)
        conn.close()
    return 0 if status == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
