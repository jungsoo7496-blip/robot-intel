"""검색 키워드 → 뉴스 수집원(sources) 물질화 (2026-09-08).

종전에는 네이버·구글 뉴스 '검색어 한 줄'이 sources 행 9개로 흩어져 있었다.
이제 검색어는 search_keywords 한 목록에 있고, 대상별 설정(우선순위·수집
간격·조회 건수)은 news_search_targets 2행에 있다. 이 모듈이 매 수집 배치
시작에 (대상 × 검색어) 조합을 sources 관리행으로 맞춘다.

가상 채널도, 행 재생성도 아닌 **행 물질화(materialize)**다 — 관리행도 그냥
sources 행이라 collect.py의 디스패치·기아 방지 정렬·source_runs 기록이
그대로 적용되고, raw_items.source_id FK와 기존 수집 이력이 한 건도 끊기지
않는다.

주의:
  · 검색어를 지워도 관리행은 **비활성화만** 한다(DELETE 금지 — raw_items FK).
    같은 검색어를 다시 넣으면 term이 같아 기존 행이 되살아난다.
  · 구글 RSS 주소를 실제로 만드는 곳은 여기뿐이다. 운영 화면(TS)의 미리보기는
    운영자 확인용 사본이며, 수집에 쓰이는 주소는 이 파일이 만든다.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from urllib.parse import quote

import psycopg

# 구글 뉴스 RSS 고정 꼬리표 (한국어·한국판). 현재 DB의 4개 행과 글자까지 같다.
GOOGLE_NEWS_SUFFIX = "&hl=ko&gl=KR&ceid=KR:ko"
GOOGLE_NEWS_BASE = "https://news.google.com/rss/search?q="
# 네이버는 API 경로라 url을 읽지 않는다 — 행을 구분하는 식별자로만 쓴다.
NAVER_URL_PREFIX = "naver-news://query/"
# 기본 식별자가 sources_url_key(url UNIQUE)에 걸릴 때 쓰는 대체 접두어.
# 기존 5행은 사람이 붙인 영문 슬러그(naver-news://query/robot)를 쓰는데,
# 운영자가 그 슬러그와 같은 글자의 검색어('robot')를 넣으면 기본 식별자가
# 그 행과 부딪힌다. 경로 한 칸만 다르게 해 옛 슬러그와 절대 겹치지 않게 한다.
NAVER_FALLBACK_PREFIX = "naver-news://keyword/"

SCOPE_FOR_USE = {
    "news": ("ALL", "NEWS"),
    "reports": ("ALL", "REPORTS"),
}


class MigrationMissing(RuntimeError):
    """마이그레이션 26 미적용 — 검색 키워드 모델이 아직 DB에 없다."""


@dataclass(frozen=True)
class Keyword:
    """검색어 1개 (search_keywords 한 행)."""

    term: str
    alt_terms: tuple[str, ...] = ()
    scope: str = "ALL"  # ALL | NEWS | REPORTS
    sort_order: int = 100


@dataclass(frozen=True)
class NewsTarget:
    """뉴스 검색 대상 1개 (news_search_targets 한 행)."""

    target_key: str
    name: str
    is_active: bool = True
    priority: int = 40
    fetch_interval_minutes: int = 100
    display: int = 300
    source_type: str = "일반 언론"
    country_region: str = "국내"
    language: str = "ko"


# ------------------------------------------------------------------
# 순수 함수 — DB·네트워크 없이 단위 테스트한다
# ------------------------------------------------------------------


def google_query(kw: Keyword) -> str:
    """구글 뉴스 검색 질의.

    alt_terms가 없으면 검색어 그대로(따옴표 없음 — 현재 DB 행과 동일).
    있으면 같은 뜻 다른 표기를 따옴표로 묶어 OR로 잇는다:
      '피지컬 AI' + {Physical AI, Embodied AI}
        → '"피지컬 AI" OR "Physical AI" OR "Embodied AI"'
    """
    terms = [kw.term, *[t for t in kw.alt_terms if t and t.strip()]]
    if len(terms) == 1:
        return terms[0]
    return " OR ".join(f'"{t.strip()}"' for t in terms)


def google_news_rss_url(kw: Keyword) -> str:
    """검색어에서 구글 뉴스 RSS 주소를 만든다 (요구 1-2).

    quote(safe='')여야 공백이 %20이 되어 현재 4개 구글 행의 URL과 글자까지
    같아진다 (quote_plus의 '+'나 safe 기본값의 '/'는 다른 주소가 된다).
    tests/unit/test_search_keywords.py가 그 4개를 고정한다.
    """
    return GOOGLE_NEWS_BASE + quote(google_query(kw), safe="") + GOOGLE_NEWS_SUFFIX


def naver_source_url(kw: Keyword) -> str:
    """네이버 관리행의 식별용 url. NAVER_API 경로는 이 값을 읽지 않는다."""
    return NAVER_URL_PREFIX + quote(kw.term, safe="")


def naver_fallback_url(kw: Keyword) -> str:
    """기본 식별용 url이 이미 다른 행에 쓰이고 있을 때의 대체 url (네이버 전용).

    예: 옛 행이 '로봇'을 naver-news://query/robot 로 갖고 있는데 운영자가
    검색어 'robot'을 새로 넣으면 기본 식별자가 그 행과 부딪힌다. NAVER_API
    수집은 url을 읽지 않으므로(adapter_config.query로 검색한다) 식별자만
    비켜 주면 그 검색어도 정상 수집된다. 'query/' 대신 'keyword/'를 쓰고
    검색어를 그대로 인코딩하므로 옛 슬러그와도, 다른 검색어와도 겹치지 않는다.
    """
    return NAVER_FALLBACK_PREFIX + quote(kw.term, safe="")


def source_name(target_key: str, kw: Keyword) -> str:
    prefix = "네이버 뉴스" if target_key == "naver" else "Google 뉴스"
    return f"{prefix} — {kw.term}"


def adapter_config_for(target: NewsTarget, kw: Keyword) -> dict:
    """수집 어댑터가 읽는 설정 (sources.adapter_config)."""
    if target.target_key == "naver":
        return {"query": kw.term, "display": target.display}
    return {"link_decoder": "google_news"}


def fetch_method_for(target_key: str) -> str:
    """관리행의 fetch_method — collect.py 디스패치는 손대지 않는다."""
    return "NAVER_API" if target_key == "naver" else "RSS"


def _row_to_keyword(row: dict) -> Keyword:
    return Keyword(
        term=row["term"],
        alt_terms=tuple(row.get("alt_terms") or ()),
        scope=row.get("scope") or "ALL",
        sort_order=int(row.get("sort_order") or 100),
    )


def _row_to_target(row: dict) -> NewsTarget:
    return NewsTarget(
        target_key=row["target_key"],
        name=row["name"],
        is_active=bool(row["is_active"]),
        priority=int(row["priority"]),
        fetch_interval_minutes=int(row["fetch_interval_minutes"]),
        display=int(row["display"]),
        source_type=row["source_type"],
        country_region=row["country_region"],
        language=row["language"],
    )


def keywords_for_use(rows: list[dict], use: str) -> list[Keyword]:
    """scope 필터 + 정렬 (순수 함수 — load_keywords의 파이썬 부분)."""
    allowed = SCOPE_FOR_USE.get(use)
    if allowed is None:
        raise ValueError(f"use는 'news' 또는 'reports'여야 합니다: {use!r}")
    picked = [_row_to_keyword(r) for r in rows if (r.get("scope") or "ALL") in allowed]
    picked.sort(key=lambda k: (k.sort_order, k.term))
    return picked


# ------------------------------------------------------------------
# DB 접근
# ------------------------------------------------------------------


def model_available(conn: psycopg.Connection) -> bool:
    """마이그레이션 26이 적용됐는지 한 번의 왕복으로 확인한다.

    예외 → rollback 대신 미리 확인하는 이유: 배치가 이미 진행한 작업을
    되돌리지 않기 위해서다.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT to_regclass('public.search_keywords') IS NOT NULL
               AND to_regclass('public.news_search_targets') IS NOT NULL
               AND EXISTS (
                     SELECT 1 FROM information_schema.columns
                     WHERE table_schema = 'public' AND table_name = 'sources'
                       AND column_name = 'managed_by') AS ok
            """
        )
        return bool(cur.fetchone()["ok"])


def load_keywords(conn: psycopg.Connection, *, use: str) -> list[Keyword]:
    """use='news' → scope IN ('ALL','NEWS') / 'reports' → ('ALL','REPORTS')."""
    allowed = SCOPE_FOR_USE.get(use)
    if allowed is None:
        raise ValueError(f"use는 'news' 또는 'reports'여야 합니다: {use!r}")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT term, alt_terms, scope, sort_order
            FROM public.search_keywords
            WHERE scope = ANY(%s)
            ORDER BY sort_order ASC, term ASC
            """,
            (list(allowed),),
        )
        return [_row_to_keyword(r) for r in cur.fetchall()]


def load_news_targets(conn: psycopg.Connection) -> dict[str, NewsTarget]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT target_key, name, is_active, priority, fetch_interval_minutes,
                   display, source_type, country_region, language
            FROM public.news_search_targets
            ORDER BY target_key
            """
        )
        return {r["target_key"]: _row_to_target(r) for r in cur.fetchall()}


def _run_upsert(
    cur: psycopg.Cursor, sql: str, params: tuple
) -> tuple[str | None, psycopg.Error | None]:
    """조합 1개를 세이브포인트 안에서 시도한다.

    성공하면 ('created'|'updated', None), 실패하면 (None, 예외)를 돌려준다.
    실패한 한 건만 되돌리므로 나머지 조합은 그대로 반영된다.
    """
    cur.execute("SAVEPOINT kw_sync")
    try:
        cur.execute(sql, params)
        inserted = bool(cur.fetchone()["inserted"])
    except psycopg.Error as e:
        cur.execute("ROLLBACK TO SAVEPOINT kw_sync")
        cur.execute("RELEASE SAVEPOINT kw_sync")
        return None, e
    cur.execute("RELEASE SAVEPOINT kw_sync")
    return ("created" if inserted else "updated"), None


def _upsert_source(
    conn: psycopg.Connection, target: NewsTarget, kw: Keyword
) -> str:
    """관리행 1개를 맞춘다. 'created' | 'updated' | 'skipped'를 돌려준다.

    ON CONFLICT의 WHERE managed_by='keyword_search'는 부분 유니크 인덱스
    (sources_keyword_search_uniq) 추론에 반드시 필요하다 — 빼면 런타임에
    '고유 인덱스 없음' 오류가 난다.

    url은 구글만 갱신한다. 네이버 기존 5행은 사람이 붙인 영문 슬러그
    (naver-news://query/robot)를 쓰고 있고 NAVER_API 경로는 url을 읽지 않아,
    굳이 바꾸면 sources_url_key만 흔들린다.
    source_type/country_region/language도 INSERT 때만 대상 기본값을 쓴다
    (운영자가 손댄 기존 값 보존).

    그 영문 슬러그와 같은 글자의 검색어('robot')를 운영자가 넣으면 네이버
    식별자가 url UNIQUE에 걸린다. 화면은 '추가했습니다'라고 하는데 수집은
    영영 안 되는 자리라, 네이버는 대체 식별자로 한 번 더 시도한다
    (naver_fallback_url). 구글은 url이 곧 수집 주소라 바꿀 수 없어 그대로
    건너뛴다.
    """
    is_google = target.target_key == "google"
    url_update = "url = EXCLUDED.url," if is_google else ""
    sql = f"""
        INSERT INTO public.sources (
          name, source_type, country_region, language, url, fetch_method,
          adapter_config, fetch_interval_minutes, priority, is_active,
          managed_by, search_target, keyword_term
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, true,
                'keyword_search', %s, %s)
        ON CONFLICT (search_target, keyword_term) WHERE managed_by = 'keyword_search'
        DO UPDATE SET
          name = EXCLUDED.name,
          fetch_method = EXCLUDED.fetch_method,
          adapter_config = EXCLUDED.adapter_config,
          {url_update}
          fetch_interval_minutes = EXCLUDED.fetch_interval_minutes,
          priority = EXCLUDED.priority,
          is_active = true,
          updated_at = now()
        RETURNING (xmax = 0) AS inserted
    """
    url = google_news_rss_url(kw) if is_google else naver_source_url(kw)

    def params_for(value: str) -> tuple:
        return (
            source_name(target.target_key, kw),
            target.source_type,
            target.country_region,
            target.language,
            value,
            fetch_method_for(target.target_key),
            json.dumps(adapter_config_for(target, kw), ensure_ascii=False),
            target.fetch_interval_minutes,
            target.priority,
            target.target_key,
            kw.term,
        )

    # 조합 하나가 실패해도(예: 다른 수집원이 같은 url을 이미 쓰고 있음) 나머지
    # 조합은 반영되어야 한다 — 세이브포인트로 그 한 건만 되돌린다.
    with conn.cursor() as cur:
        outcome, err = _run_upsert(cur, sql, params_for(url))

        # 네이버 식별자가 옛 슬러그와 부딪힌 경우에만 대체 식별자로 재시도한다.
        if (
            outcome is None
            and not is_google
            and isinstance(err, psycopg.errors.UniqueViolation)
        ):
            fallback = naver_fallback_url(kw)
            print(
                f"[collect] 검색 키워드 '{kw.term}'(naver) 식별 주소 {url} 는 "
                f"이미 다른 수집원이 쓰고 있어 {fallback} 로 등록합니다"
                " (네이버 수집은 이 주소를 읽지 않아 영향 없음)",
                file=sys.stderr,
            )
            outcome, err = _run_upsert(cur, sql, params_for(fallback))

    if outcome is None:
        print(
            f"[collect] 검색 키워드 '{kw.term}'({target.target_key}) 반영 실패"
            f"(건너뜀): {err}",
            file=sys.stderr,
        )
        return "skipped"
    return outcome


def sync_news_search_sources(conn: psycopg.Connection) -> dict[str, int]:
    """검색어 × 활성 대상 → sources 관리행을 맞춘다.

    반환 {'created', 'updated', 'deactivated', 'skipped'}.
    커밋까지 여기서 한다 — 실패 시 호출자가 rollback하고 기존 수집원으로 계속한다.
    """
    if not model_available(conn):
        raise MigrationMissing(
            "검색 키워드 표가 없습니다 "
            "(supabase/migrations/20260908000026_search_keywords.sql 적용 필요)"
        )

    targets = load_news_targets(conn)
    keywords = load_keywords(conn, use="news")
    stats = {"created": 0, "updated": 0, "deactivated": 0, "skipped": 0}

    wanted: list[tuple[str, str]] = []
    for target_key in sorted(targets):
        target = targets[target_key]
        if not target.is_active:
            continue
        for kw in keywords:
            wanted.append((target.target_key, kw.term))
            stats[_upsert_source(conn, target, kw)] += 1

    # 정리: 원하지 않는 조합은 비활성화만 한다 (DELETE 금지 — raw_items FK).
    # 비활성 대상(is_active=false)의 행도 wanted에 없으므로 여기서 꺼진다.
    with conn.cursor() as cur:
        if wanted:
            cur.execute(
                """
                UPDATE public.sources
                SET is_active = false, updated_at = now()
                WHERE managed_by = 'keyword_search'
                  AND is_active
                  AND (search_target, keyword_term) NOT IN (
                        SELECT * FROM unnest(%s::text[], %s::text[]))
                """,
                ([t for t, _ in wanted], [k for _, k in wanted]),
            )
        else:
            cur.execute(
                """
                UPDATE public.sources
                SET is_active = false, updated_at = now()
                WHERE managed_by = 'keyword_search' AND is_active
                """
            )
        stats["deactivated"] = cur.rowcount
    conn.commit()
    return stats
