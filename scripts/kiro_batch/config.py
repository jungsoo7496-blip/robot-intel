"""환경변수·app_settings 기반 런타임 설정.

모델명과 처리 한도는 코드 수정 없이 변경할 수 있어야 한다 (AIR-004, tasks §1.3).
우선순위: 환경변수 > app_settings > 기본값.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def parse_bool(value: object) -> bool:
    """환경변수·app_settings(jsonb)의 불리언 해석 — "false"/"0"/"off"는 거짓.

    bool은 int의 하위 타입이라 int() 변환 경로에 섞이면 "false"가 예외를
    낸다. 보존 정책 플래그(keep_*)가 처음으로 불리언 설정을 도입했다 (2026-09-08).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


@dataclass
class Settings:
    # Supabase / DB
    supabase_db_url: str = ""

    # Gemini
    gemini_api_key: str = ""

    # 네이버 뉴스 검색 API
    naver_client_id: str = ""
    naver_client_secret: str = ""
    article_model: str = "gemini-flash-lite-latest"
    # 병렬 스트림에 쓸 모델 목록 (쉼표 구분). 무료 Lite 2종은 서로 독립
    # 쿼터 풀이라(실측 확인) 같은 배치 시간 안에서 처리량이 2배가 된다.
    # 하나만 두면 기존 단일 스트림과 동일하게 동작한다.
    article_models: str = "gemini-flash-lite-latest,gemini-3.1-flash-lite"
    # 모델당 워커 수. 생성이 5~7초 걸려 워커 1개로는 분당 8~10요청밖에
    # 못 쓴다(한도 15). 2개를 겹쳐 돌리고 속도는 아래 RPM으로 제한한다.
    article_stream_workers: int = 2
    # 모델당 목표 분당 요청 수 (무료 한도 15의 80% — 여유 마진)
    article_target_rpm: float = 12.0
    brief_model: str = "gemini-flash-latest"
    brief_fallback_model: str = "gemini-flash-lite-latest"

    # 배치 처리 한도 (설계 §3.3, §9.5 — 2026-08-07 큐 적체로 상향)
    # ※ 여기 기본값은 app_settings에 같은 키가 있으면 그쪽에 진다
    #   (우선순위: 환경변수 > app_settings > 기본값). 2026-09-08 현재 운영
    #   app_settings는 article_daily_soft_limit=490 · brief_daily_reserve=10
    #   (운영자 승인으로 상향) · article_batch_max_count=280 ·
    #   article_batch_max_seconds=420이다. 아래 기본값은 그 키가 없을 때만 쓰인다.
    # ※ 사무실 PC에서 직접 돌릴 때는 scripts/.env의 환경변수가 app_settings보다
    #   먼저다 — 거기 값이 낡으면 로컬만 다른 한도로 돈다.

    # 오류(429·5xx·잘못된 출력) 뒤 한 박자 쉬는 간격. 평상시 호출 속도는
    # article_target_rpm의 RateLimiter가 정하므로 이 값은 정상 경로에
    # 관여하지 않는다 (2026-09-08 주석 정정 — 예전엔 고정 간격이었다).
    article_request_interval_seconds: float = 4.5
    # 건수 캡은 '폭주 방지'용이지 처리량 제한이 아니다 — 시간 예산이 먼저
    # 걸려야 Actions 분이 예측 가능하다 (2026-08-12, 사용자 지적으로 정립).
    # 2026-09-08 상향(280 → 400): 09-08 09:16 배치(예산 720초)가 캡 280에
    # 걸려 279건에서 끝났다. 속도 상한이 모델당 12 RPM × 2모델 = 24건/분이라
    # 720초의 이론 최대치는 288건 — 캡을 풀어 더 얻을 수 있던 건 많아야
    # 9건이다. 게다가 그 실행은 duration 758초로 예산을 이미 넘긴 뒤였다
    # (workflow_usage 실측). 즉 처리량 회복이 아니라, 두 상한이 같이 걸려
    # '무엇이 배치를 끝냈는지' 못 읽던 상태를 푸는 정리다.
    # 운영에서 실제로 캡을 밀어 올리는 건 analyze.runaway_cap_floor다 —
    # app_settings(280)를 반영한 뒤 시간 예산에 맞춰 하한을 다시 계산한다
    # (720초 예산이면 292). 그래서 예산을 올릴 때 이 값을 같이 못 올려도
    # 캡이 먼저 걸리지는 않는다.
    article_batch_max_count: int = 400
    # 2026-08-08 상향: 유입(일 ~200건)과 처리(회당 ~40건×6회)가 손익분기라
    # 백로그가 안 줄던 문제 — 회당 7분으로 늘려 일 ~310건 처리 여력 확보
    article_batch_max_seconds: int = 420  # 7분 — 워크플로 timeout 11분 안
    # 모델당 하드 한도 500의 98% (2026-09-08 상향: 450 → 490, 운영자 승인.
    # app_settings도 490으로 함께 올렸다).
    # 한도 500은 실측으로 확인됐다 — 08-11 gemini-flash-lite-latest가 성공
    # 499콜을 찍고 그 뒤 429 19건을 받았다(gemini_calls). 넘겨도 손실은 없고
    # 429 몇 건과 다음 쿼터일로의 이월이 전부다.
    # 실측 소진량(gemini_calls, 쿼터일 09-01~09-06)은 모델당 209~293콜인데
    # 09-07 쿼터일은 하루가 끝나기도 전에 386·382콜까지 갔다(429는 0건).
    # 여유가 예전만큼 넉넉하지는 않으니 더 올릴 땐 최근 쿼터일을 다시 볼 것.
    # 넘겨서 429가 나도 손실이 아니다 — retry_job_after_rate_limit이 job을
    # RETRY로 되돌리고 (30초 → 2분 → 3시간) 연속 3회면 배치를 접는다.
    # 우리 카운터는 성공·429·오류를 모두 세므로 구글 집계보다 크면 컸지
    # 작지 않다 (gemini_provider에 내부 재시도가 없어 호출 1건 = 요청 1건,
    # 그리고 analyze._record_call이 리셋을 걸친 호출을 늦은 쪽 쿼터일에
    # 얹는다 — 2026-09-08 결함 I 수정 전에는 이 전제가 깨져 있었다:
    # 쿼터일을 배치 시작 때 한 번만 잡아, 리셋 뒤 호출이 어제로 쌓였다).
    article_daily_soft_limit: int = 490
    # 브리프·보고서가 예비 모델(=Flash Lite, 기사와 같은 쿼터 풀)로 넘어올
    # 때의 몫. 기사 분석은 (소프트리밋 - 이 값)에서 멈춘다 = 480콜.
    # 2026-09-08 상향(5 → 10): 기사 분석 외 호출의 실측 최대가 하루 7건이라
    # (최근 14일 gemini_calls, 대부분의 날은 0건) 5로는 여유가 없었다.
    brief_daily_reserve: int = 10
    # 야간(KST 17시~익일 6시)에 남겨둘 오전 몫 — 조간 뉴스 분석 보장 (quota.py).
    # 쿼터일은 KST 17시에 시작하는데 독자의 하루는 아침이라, 야간 배치가 다
    # 태우면 조간이 굶는다. 야간 상한 = 소프트리밋 - 이 값.
    # 2026-09-08 점검: 지금은 걸리지 않는다 — 야간 슬롯(22:50·05:10) 예산
    # 합계 600초로는 모델당 약 117콜만 쓰는데 야간 상한은 340콜
    # (기사 분석은 브리프 예약을 더 뺀 330콜에서 멈춘다)이다.
    # 야간 배치 시간이 하루 합계 약 1,670초를 넘길 때부터 실제로 제동이 걸린다.
    morning_reserve_calls: int = 150

    # 콘텐츠 처리
    raw_text_retention_days: int = 30
    clean_text_max_length: int = 12000
    title_similarity_threshold: int = 85

    # 보존 정책 (2026-09-08 데이터 관리 — DB 500 MB 한도 대응).
    # app_settings 동일 키를 운영 화면(/admin/data)에서 편집한다. 0 = 안 함.
    # 본문 추출 대기가 발행(없으면 수집) 후 며칠을 넘기면 EXPIRED로 정리할지
    extract_expire_days: int = 3
    # 분석 대기 job이 원문 발행 후 며칠을 넘기면 CANCELLED로 정리할지
    analysis_expire_days: int = 3
    # 클러스터 비대표 구성원 본문(clean_text)을 며칠 뒤 비울지 —
    # publish.MERGE_WINDOW_DAYS(7)보다 짧게 잡아도 cleanup이 7일로 올려 쓴다
    nonrep_body_retention_days: int = 7
    # 로컬 필터 EXCLUDE 판정 항목의 본문을 저장·보존할지 (읽는 코드 없음)
    keep_exclude_body: bool = False
    # Gemini 응답 원문(analyses.raw_response)을 저장할지 — PASS가 아닌
    # 분석(WARN·FAIL)은 이 값과 무관하게 디버깅용으로 보존한다
    keep_raw_response: bool = False

    # 수집
    fetch_timeout_seconds: float = 20.0
    fetch_max_bytes: int = 3_000_000
    # 수집 배치를 두 구간으로 분리해 후처리 시간을 보장한다 (외부 리뷰 2차):
    # fetch(수집원 호출)와 process(본문 추출·필터·클러스터) 각각의 예산
    fetch_budget_seconds: int = 240
    process_budget_seconds: int = 180
    # 본문 내려받기 동시 실행 수 (2026-08-11). 순수 네트워크 대기라 병렬이
    # 효과적이다. 상대 사이트에 몰리지 않게 8을 넘기지 않는다.
    extract_workers: int = 8
    # 구글 뉴스 링크 해독 동시 실행 수. 라이브러리가 호출마다 1초를 쉬므로
    # 병렬이 곧 처리량이지만, 상대 서버 부담을 생각해 보수적으로 둔다.
    decode_workers: int = 4

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.supabase_db_url = os.environ.get("SUPABASE_DB_URL", "")
        s.gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
        s.naver_client_id = os.environ.get("NAVER_CLIENT_ID", "")
        s.naver_client_secret = os.environ.get("NAVER_CLIENT_SECRET", "")

        env_map = {
            "article_model": "ARTICLE_MODEL",
            "article_models": "ARTICLE_MODELS",
            "article_stream_workers": "ARTICLE_STREAM_WORKERS",
            "article_target_rpm": "ARTICLE_TARGET_RPM",
            "brief_model": "BRIEF_MODEL",
            "brief_fallback_model": "BRIEF_FALLBACK_MODEL",
            "article_request_interval_seconds": "ARTICLE_REQUEST_INTERVAL_SECONDS",
            "article_batch_max_count": "ARTICLE_BATCH_MAX_COUNT",
            "article_batch_max_seconds": "ARTICLE_BATCH_MAX_SECONDS",
            "article_daily_soft_limit": "ARTICLE_DAILY_SOFT_LIMIT",
            "brief_daily_reserve": "BRIEF_DAILY_RESERVE",
            "morning_reserve_calls": "MORNING_RESERVE_CALLS",
            "raw_text_retention_days": "RAW_TEXT_RETENTION_DAYS",
            "clean_text_max_length": "CLEAN_TEXT_MAX_LENGTH",
            "title_similarity_threshold": "TITLE_SIMILARITY_THRESHOLD",
            "fetch_budget_seconds": "FETCH_BUDGET_SECONDS",
            "process_budget_seconds": "PROCESS_BUDGET_SECONDS",
            "extract_workers": "EXTRACT_WORKERS",
            "decode_workers": "DECODE_WORKERS",
            "extract_expire_days": "EXTRACT_EXPIRE_DAYS",
            "analysis_expire_days": "ANALYSIS_EXPIRE_DAYS",
            "nonrep_body_retention_days": "NONREP_BODY_RETENTION_DAYS",
            "keep_exclude_body": "KEEP_EXCLUDE_BODY",
            "keep_raw_response": "KEEP_RAW_RESPONSE",
        }
        for attr, env_key in env_map.items():
            raw = os.environ.get(env_key)
            if raw is None or raw == "":
                continue
            current = getattr(s, attr)
            if isinstance(current, bool):  # int보다 먼저 — bool은 int의 하위 타입
                setattr(s, attr, parse_bool(raw))
            elif isinstance(current, float):
                setattr(s, attr, float(raw))
            elif isinstance(current, int):
                setattr(s, attr, int(raw))
            else:
                setattr(s, attr, raw)
        return s

    def apply_app_settings(self, rows: dict[str, object]) -> None:
        """app_settings 테이블 값을 반영한다. 환경변수가 이미 지정한 값은 유지."""
        mapping = {
            "article_model": "article_model",
            "article_models": "article_models",
            "article_stream_workers": "article_stream_workers",
            "article_target_rpm": "article_target_rpm",
            "morning_reserve_calls": "morning_reserve_calls",
            "brief_model": "brief_model",
            "brief_fallback_model": "brief_fallback_model",
            "article_request_interval_seconds": "article_request_interval_seconds",
            "article_batch_max_count": "article_batch_max_count",
            "article_batch_max_seconds": "article_batch_max_seconds",
            "article_daily_soft_limit": "article_daily_soft_limit",
            "brief_daily_reserve": "brief_daily_reserve",
            "raw_text_retention_days": "raw_text_retention_days",
            "clean_text_max_length": "clean_text_max_length",
            "title_similarity_threshold": "title_similarity_threshold",
            "extract_expire_days": "extract_expire_days",
            "analysis_expire_days": "analysis_expire_days",
            "nonrep_body_retention_days": "nonrep_body_retention_days",
            "keep_exclude_body": "keep_exclude_body",
            "keep_raw_response": "keep_raw_response",
        }
        for key, attr in mapping.items():
            if key not in rows:
                continue
            env_key = key.upper()
            if os.environ.get(env_key):
                continue  # 환경변수 우선
            value = rows[key]
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except (ValueError, TypeError):
                    pass
            current = getattr(self, attr)
            if isinstance(current, bool):  # int보다 먼저 — bool은 int의 하위 타입
                setattr(self, attr, parse_bool(value))
            elif isinstance(current, float):
                setattr(self, attr, float(value))
            elif isinstance(current, int):
                setattr(self, attr, int(value))
            else:
                setattr(self, attr, str(value))

    def require(self, *names: str) -> None:
        """필수 설정 누락 시 명확한 오류를 낸다 (tasks §1.2 검증)."""
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise RuntimeError(
                "필수 설정이 없습니다: "
                + ", ".join(missing)
                + " — .env 또는 GitHub Secrets를 확인하세요."
            )
