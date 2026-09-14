"""수집 배치 진입점 (FR-002, FR-003, tasks §5).

흐름: RSS 수집 → URL 정규화 → 신규 저장 → 본문 추출 → 로컬 필터
      → 클러스터링 → 분석 큐 등록

실행: python -m kiro_batch.collect
한 수집원의 오류는 다른 수집원을 중단시키지 않는다.
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import feedparser
import httpx
import psycopg

from .clustering import assign_to_cluster, fetch_cluster_candidates
from .config import Settings
from .db import connect, load_app_settings
from .extract import FetchError, extract_clean_text, fetch_html
from .freshness import STARVATION_QUOTA_RATIO, clamp_source_time
from .list_page import collect_list_page
from .local_filter import LocalFilter
from .naver_news import collect_naver_news
from .queue import SOURCE_TYPE_PRIORITY, count_pending, enqueue_article_job
from .url_normalize import normalize_url
from .usage import UsageRecorder, close_stale_runs


def bulk_insert_raw_items(conn, columns: list[str], rows: list[tuple]) -> int:
    """수집 항목을 한 번의 쿼리로 저장하고 신규 건수를 반환한다 (2026-08-11).

    종전에는 기사 한 건마다 INSERT + commit을 했다. GitHub 러너(미국)와
    Supabase(서울) 사이는 왕복이 길어(측정 기준 건당 약 0.25초 × 2회) 배치당
    350건이면 그것만으로 175초가 든다 — 수집 예산 330초의 절반이 순수 대기였다.
    한 번에 묶으면 수집원당 2회 왕복으로 줄어 사실상 0이 된다.

    같은 배치 안의 중복 canonical_url은 미리 걸러낸다 (ON CONFLICT는 문장
    사이의 충돌만 처리하므로 문장 안 중복은 남겨두면 낭비다).
    """
    if not rows:
        return 0
    canonical_idx = columns.index("canonical_url")
    seen: set[str] = set()
    unique: list[tuple] = []
    for row in rows:
        key = row[canonical_idx]
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)

    placeholders = ", ".join(["(" + ", ".join(["%s"] * len(columns)) + ")"] * len(unique))
    sql = (
        f"INSERT INTO raw_items ({', '.join(columns)}) VALUES {placeholders} "
        "ON CONFLICT (canonical_url) DO NOTHING RETURNING id"
    )
    params = [value for row in unique for value in row]
    with conn.cursor() as cur:
        cur.execute(sql, params)
        inserted = len(cur.fetchall())
    conn.commit()
    return inserted


def _parse_feed_datetime(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


def _decode_link(link: str, decoder: str | None) -> str | None:
    """뉴스 애그리게이터 리다이렉트 링크를 원문 URL로 변환한다.

    Google 뉴스 RSS 링크는 인코딩되어 있어 디코딩 없이는
    원문 본문을 추출할 수 없다. 실패한 항목은 건너뛴다.
    """
    if decoder != "google_news":
        return link
    try:
        from googlenewsdecoder import gnewsdecoder

        result = gnewsdecoder(link, interval=1)
        if result.get("status") and result.get("decoded_url"):
            return result["decoded_url"]
    except Exception as e:  # noqa: BLE001
        print(f"[collect] 구글뉴스 링크 디코딩 실패(건너뜀): {e}", file=sys.stderr)
    return None


def collect_rss_source(
    conn, source: dict, settings: Settings, deadline: float | None = None
) -> tuple[int, int, bool]:
    """한 RSS 수집원 처리. (전체 항목 수, 신규 저장 수, 시간초과 여부)를 반환한다."""
    with httpx.Client(
        timeout=settings.fetch_timeout_seconds, follow_redirects=True,
        headers={"User-Agent": "KIRO-RobotIntel/0.1 (internal research bot)"},
    ) as client:
        response = client.get(source["url"])
        response.raise_for_status()
        feed = feedparser.parse(response.content)

    if feed.bozo and not feed.entries:
        raise RuntimeError(f"RSS 파싱 실패: {feed.bozo_exception}")

    adapter_config = source.get("adapter_config") or {}
    link_decoder = adapter_config.get("link_decoder")

    fetched, new, partial = 0, 0, False
    pending_rows: list[tuple] = []

    # 이미 처리한 애그리게이터 항목은 재디코딩하지 않는다 (외부 리뷰 2차).
    # 종전에는 항목마다 한 번씩 조회했는데, 피드 하나가 100건이면 왕복만
    # 100회다 — 한 번에 확인한다 (2026-08-11).
    known_guids: set[str] = set()
    if link_decoder:
        guids = [
            getattr(e, "id", None) or getattr(e, "link", None) for e in feed.entries
        ]
        guids = [g for g in guids if g]
        if guids:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT aggregator_guid FROM raw_items WHERE aggregator_guid = ANY(%s)",
                    (guids,),
                )
                known_guids = {r["aggregator_guid"] for r in cur.fetchall()}

    # 디코딩 대상만 추린다 (이미 아는 guid는 건너뜀)
    todo: list[tuple] = []  # (link, guid, entry)
    for entry in feed.entries:
        link = getattr(entry, "link", None)
        if not link:
            continue
        fetched += 1
        guid = getattr(entry, "id", None) or link
        if link_decoder and guid in known_guids:
            continue
        todo.append((link, guid, entry))

    # 구글 뉴스 링크는 해독 라이브러리가 호출마다 1초를 강제로 쉬어서
    # 100건이면 그것만 100초다. 병렬로 돌려 그 대기를 겹친다 (2026-08-11).
    # 상대 서버 부담을 감안해 워커 수는 보수적으로 둔다.
    decoded: list[str | None]
    if link_decoder and len(todo) > 1:
        workers = max(1, min(settings.decode_workers, len(todo)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            decoded = list(pool.map(lambda t: _decode_link(t[0], link_decoder), todo))
    else:
        decoded = [_decode_link(t[0], link_decoder) for t in todo]

    for (raw_link, guid, entry), link in zip(todo, decoded):
        if deadline is not None and time.monotonic() > deadline:
            print(f"[collect] fetch 예산 소진 — {source['name']} 잔여 항목은 다음 배치로")
            partial = True
            break
        if not link:
            continue
        pending_rows.append(
            (
                source["id"],
                link,
                normalize_url(link),
                getattr(entry, "title", None),
                getattr(entry, "author", None),
                getattr(entry, "summary", None),
                _parse_feed_datetime(entry),
                guid,
            )
        )

    new = bulk_insert_raw_items(
        conn,
        ["source_id", "url", "canonical_url", "title", "author", "feed_summary",
         "published_at", "aggregator_guid"],
        pending_rows,
    )
    return fetched, new, partial


def collect_list_page_source(
    conn, source: dict, settings: Settings
) -> tuple[int, int, bool]:
    """목록 페이지 수집원 처리 (tasks §5.2). 선택자는 adapter_config로 정의."""
    adapter_config = source.get("adapter_config") or {}
    items = collect_list_page(
        source["url"], adapter_config, timeout=settings.fetch_timeout_seconds
    )

    rows = [
        (source["id"], item.url, normalize_url(item.url), item.title, item.summary)
        for item in items
    ]
    new = bulk_insert_raw_items(
        conn,
        ["source_id", "url", "canonical_url", "title", "feed_summary"],
        rows,
    )
    return len(items), new, False


def collect_naver_source(
    conn, source: dict, settings: Settings
) -> tuple[int, int, bool]:
    """네이버 뉴스 검색 API 수집원 처리."""
    adapter_config = source.get("adapter_config") or {}
    items = collect_naver_news(
        settings.naver_client_id,
        settings.naver_client_secret,
        query=adapter_config.get("query", "로봇"),
        # 기본 300 = 3페이지 (2026-08-15). 100이면 병목 검색어의 피드창이
        # 10.5시간뿐이라 04:30 수집 폐지·주말 감축을 못 견딘다 (naver_news.py)
        display=int(adapter_config.get("display", 300)),
        timeout=settings.fetch_timeout_seconds,
    )

    rows = [
        (
            source["id"], item.url, normalize_url(item.url),
            item.title, item.summary, item.published_at,
        )
        for item in items
    ]
    new = bulk_insert_raw_items(
        conn,
        ["source_id", "url", "canonical_url", "title", "feed_summary", "published_at"],
        rows,
    )
    return len(items), new, False


def run_source(
    conn, source: dict, settings: Settings, deadline: float | None = None
) -> tuple[int, int]:
    """수집원 1개 실행 + source_runs 기록. 오류는 격리한다."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO source_runs (source_id, workflow_run_id)
            VALUES (%s, %s) RETURNING id
            """,
            (source["id"], None),
        )
        run_id = cur.fetchone()["id"]
    conn.commit()

    try:
        if source["fetch_method"] == "RSS":
            fetched, new, partial = collect_rss_source(conn, source, settings, deadline)
        elif source["fetch_method"] == "LIST_PAGE":
            fetched, new, partial = collect_list_page_source(conn, source, settings)
        elif source["fetch_method"] == "NAVER_API":
            fetched, new, partial = collect_naver_source(conn, source, settings)
        else:
            raise NotImplementedError(
                f"수집 방식 미구현: {source['fetch_method']} — 어댑터 추가 필요"
            )
        with conn.cursor() as cur:
            # 예산 소진으로 중단된 실행은 SUCCESS가 아니라 PARTIAL로 기록
            cur.execute(
                """
                UPDATE source_runs
                SET completed_at = now(), status = %s,
                    fetched_count = %s, new_count = %s
                WHERE id = %s
                """,
                ("PARTIAL" if partial else "SUCCESS", fetched, new, run_id),
            )
            cur.execute(
                "UPDATE sources SET last_success_at = now(), last_error_message = NULL WHERE id = %s",
                (source["id"],),
            )
        conn.commit()
        return fetched, new
    except Exception as e:  # noqa: BLE001 — 수집원별 오류 격리 (FR-002)
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE source_runs
                SET completed_at = now(), status = 'FAILURE', error_message = left(%s, 1000)
                WHERE id = %s
                """,
                (str(e), run_id),
            )
            cur.execute(
                """
                UPDATE sources
                SET last_failure_at = now(), last_error_message = left(%s, 1000)
                WHERE id = %s
                """,
                (str(e), source["id"]),
            )
        conn.commit()
        print(f"[collect] 수집원 실패(계속 진행): {source['name']}: {e}", file=sys.stderr)
        return 0, 0


def _fetch_body(item: dict, settings: Settings) -> tuple[str | None, str]:
    """본문 1건 내려받기 — 순수 네트워크. DB를 절대 건드리지 않는다."""
    try:
        html = fetch_html(
            item["url"],
            timeout=settings.fetch_timeout_seconds,
            max_bytes=settings.fetch_max_bytes,
        )
        clean_text = extract_clean_text(html)
        return clean_text, ("OK" if clean_text else "META_ONLY")
    except FetchError as e:
        print(f"[collect] 본문 추출 실패(보존): {item['url']}: {e}", file=sys.stderr)
        return None, "FAILED"


def _fetch_bodies(
    items: list[dict], settings: Settings, deadline: float | None
) -> dict:
    """본문을 병렬로 받아 {raw_item_id: (clean_text, status)}로 돌려준다.

    예산(deadline)을 넘기면 그때까지 받은 것만 반환하고 나머지는 PENDING으로
    남겨 다음 배치가 집는다. 청크 단위로 확인해 초과분을 최소화한다.
    """
    out: dict = {}
    workers = max(1, settings.extract_workers)
    if workers == 1:
        for item in items:
            if deadline is not None and time.monotonic() > deadline:
                break
            out[item["id"]] = _fetch_body(item, settings)
        return out

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(items), workers):
            if deadline is not None and time.monotonic() > deadline:
                break
            chunk = items[start : start + workers]
            results = pool.map(lambda it: _fetch_body(it, settings), chunk)
            for item, res in zip(chunk, results):
                out[item["id"]] = res
    return out


def pending_sort_key(item: dict) -> datetime:
    """대기 선정 재정렬 키 = coalesce(published_at, fetched_at) — SQL과 동일.

    published_at이 없는 수집원(korea.kr 등)을 datetime.min으로 밀지 않는다.
    둘 다 없으면(테스트용 dict) 최소값으로 뒤에 둔다.
    """
    return (
        item.get("published_at")
        or item.get("fetched_at")
        or datetime.min.replace(tzinfo=timezone.utc)
    )


def clean_text_to_store(
    clean_text: str | None,
    filter_status: str,
    keep_exclude_body: bool,
    max_length: int,
) -> str | None:
    """raw_items.clean_text에 저장할 값 (순수 함수 — 단위 테스트 대상).

    EXCLUDE 판정 본문은 읽는 코드가 없다(분석·클러스터 대상이 아님). 실측
    5 MB를 차지하고 있어 keep_exclude_body=false면 처음부터 저장하지 않는다.
    """
    if not clean_text:
        return None
    if filter_status == "EXCLUDE" and not keep_exclude_body:
        return None
    return clean_text[:max_length] or None


def expire_stale_pending(conn, expire_days: int) -> int:
    """발행(없으면 수집) 후 N일 넘긴 추출 대기를 EXPIRED로 정리한다 (2026-09-08).

    실측 대기 9,985건 중 77%가 발행 7일 초과였다 — 지난 뉴스에 본문 내려받기와
    Gemini 호출을 쓰지 않는다. 0 이하면 아무것도 하지 않는다. EXPIRED 행은
    cleanup이 90일 뒤 삭제한다 (클러스터 미소속만).
    """
    if expire_days <= 0:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE raw_items
                SET extract_status = 'EXPIRED'
                WHERE extract_status = 'PENDING'
                  AND coalesce(published_at, fetched_at)
                      < now() - make_interval(days => %s)
                """,
                (expire_days,),
            )
            expired = cur.rowcount
        conn.commit()
    except psycopg.errors.CheckViolation:
        # 마이그레이션 0024(extract_status에 EXPIRED 허용) 미적용 — 만료는
        # 건너뛰고 수집은 계속한다. 만료 하나 때문에 배치가 죽으면 안 된다.
        conn.rollback()
        print(
            "[collect] 추출 대기 만료 건너뜀 — raw_items.extract_status CHECK에 "
            "'EXPIRED'가 없습니다 (supabase/migrations/20260908000024 적용 필요)",
            file=sys.stderr,
        )
        return 0
    return expired


def process_new_items(
    conn, settings: Settings, usage: UsageRecorder, deadline: float | None = None
) -> None:
    """본문 추출 → 로컬 필터 → 클러스터링 → 큐 등록."""
    local_filter = LocalFilter.from_db(conn)  # keyword_rules — 실패 시 JSON 기본값

    # 오래된 대기는 선정 전에 만료시킨다 — 아래 기아 몫이 지난 뉴스에 낭비되지
    # 않게 (보존 정책 extract_expire_days, 운영 화면에서 조정).
    expired = expire_stale_pending(conn, settings.extract_expire_days)
    if expired:
        print(
            f"[collect] 추출 대기 만료(EXPIRED): {expired}건 "
            f"(발행 {settings.extract_expire_days}일 초과)"
        )

    # 추출 순서도 신선도 우선 (2026-08-11). 분석 큐만 고쳐도 여기서 오래된
    # 것부터 처리하면 오늘 기사가 애초에 큐에 도착하지 못한다.
    # 다만 순수 최신순은 밀린 것을 영원히 굶기므로, 일부는 오래된 것에 준다.
    # 2026-08-20 상향 200→400: 클러스터링 수리로 건당 2.1초→1.24초가 되자
    # 배치가 예산 420초 중 300초만 쓰고 건수 상한에 먼저 닿았다(실측 rid
    # 32307044652). 시간 예산이 멈춤을 담당해야 한다는 원칙대로 상한을
    # 여유 있게 둔다 — 420초 ÷ 1.24초 = 약 340건이 실제 처리량이 된다.
    #
    # 정렬키는 양쪽 모두 coalesce(published_at, fetched_at) (2026-09-08).
    # korea.kr처럼 published_at이 NULL인 수집원은 종전 파이썬 재정렬에서
    # datetime.min으로 맨 뒤에 밀려 예산이 닿기 전에 잘렸다 — 18일째 굶었다.
    limit = 400
    fresh_n = int(limit * (1 - STARVATION_QUOTA_RATIO))
    with conn.cursor() as cur:
        cur.execute(
            """
            (SELECT ri.id, ri.url, ri.title, ri.published_at, ri.fetched_at,
                    ri.feed_summary, s.source_type
             FROM raw_items ri
             LEFT JOIN sources s ON s.id = ri.source_id
             WHERE ri.extract_status = 'PENDING'
             ORDER BY coalesce(ri.published_at, ri.fetched_at) DESC
             LIMIT %s)
            UNION
            (SELECT ri.id, ri.url, ri.title, ri.published_at, ri.fetched_at,
                    ri.feed_summary, s.source_type
             FROM raw_items ri
             LEFT JOIN sources s ON s.id = ri.source_id
             WHERE ri.extract_status = 'PENDING'
             ORDER BY coalesce(ri.published_at, ri.fetched_at) ASC
             LIMIT %s)
            """,
            (fresh_n, limit - fresh_n),
        )
        items = cur.fetchall()
    # UNION은 순서를 보장하지 않는다 — 예산이 모자랄 때 신선한 것이 먼저
    # 처리되도록 여기서 정렬한다 (기아 몫은 이미 위에서 확보됐다).
    items.sort(key=pending_sort_key, reverse=True)

    normalize_start = time.monotonic()
    cluster_elapsed = 0.0
    # 본문 내려받기는 순수 네트워크 작업이라 병렬로 돌린다 (2026-08-11).
    # 종전에는 200건을 한 줄로 세워 받아 건당 1.2초 × 200 = 240초를 썼다.
    # DB는 이 스레드들이 건드리지 않는다 — 결과만 모아 아래에서 순차 저장한다.
    #
    # 내려받기에 예산 전부를 주면 저장·클러스터링 몫이 남지 않는다. 받아만 놓고
    # 큐에 못 넣으면 다음 배치가 같은 일을 다시 하므로 일부만 준다.
    # 비율은 실측에서 뽑았다 — 08-12 05:08 실행이 내려받기 155초 / 저장·클러스터
    # 320초로 약 33:67이었다. 병렬화 이후 병목은 내려받기가 아니라 뒤쪽이다.
    FETCH_SHARE = 0.4
    fetch_share = (
        None if deadline is None
        else time.monotonic() + (deadline - time.monotonic()) * FETCH_SHARE
    )
    bodies = _fetch_bodies(items, settings, fetch_share)

    # 클러스터 비교 후보를 배치당 1회만 읽는다 (2026-08-20 성능 수리 —
    # 종전에는 항목마다 300행을 재조회해 러너→서울 DB 왕복이 건당 ~1초).
    # 커밋도 20건 단위로 묶는다: 왕복 자체가 비용이라서다. 중간에 죽어도
    # 미커밋 항목은 PENDING 그대로라 다음 배치가 다시 집는다 (멱등).
    cluster_candidates = fetch_cluster_candidates(conn)
    COMMIT_EVERY = 20
    since_commit = 0

    for item in items:
        # 예산 검사가 여기에도 있어야 한다 (2026-08-12). 종전에는 deadline이
        # _fetch_bodies에만 걸려 있어, 본문을 받아온 뒤의 필터·저장·클러스터링
        # 200건이 무제한으로 돌았다. 실측 process 단계가 예산 180초 대비
        # 420~475초를 써서 collect 1회가 750초(예산 420초)까지 늘어났고,
        # 그만큼 Actions 분이 analyze에서 빠져나갔다.
        if deadline is not None and time.monotonic() > deadline:
            print("[collect] 내부 시간 한도 — 잔여 본문 처리는 다음 배치로 이월")
            break
        if item["id"] not in bodies:
            # 예산 소진으로 못 받은 건 — 다음 배치가 PENDING 상태 그대로 집는다
            print("[collect] 내부 시간 한도 — 잔여 원문 추출은 다음 배치로 이월")
            break
        clean_text, extract_status = bodies[item["id"]]

        # 2) 로컬 필터 (FR-004)
        body_for_filter = clean_text or item["feed_summary"] or ""
        result = local_filter.evaluate(item["title"] or "", body_for_filter)

        # raw_text는 저장하지 않는다 (2026-09-08). 종전에는 clean_text와
        # 같은 값을 두 컬럼에 바인딩해 40 MB를 복제했고 raw_text를 읽는 코드는
        # 없었다. 컬럼은 남겨 두고(스키마 변경 없음) 값만 NULL로 쓴다.
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE raw_items
                SET raw_text = NULL, clean_text = %s,
                    extract_status = %s, filter_status = %s, filter_reason = %s
                WHERE id = %s
                """,
                (
                    clean_text_to_store(
                        clean_text,
                        result.status,
                        settings.keep_exclude_body,
                        settings.clean_text_max_length,
                    ),
                    extract_status,
                    result.status,
                    result.reason,
                    item["id"],
                ),
            )

        # 3) 클러스터링 + 큐 등록 (EXCLUDE 제외)
        if result.status in ("PASS", "LOW_PRIORITY"):
            cluster_start = time.monotonic()
            item_for_cluster = dict(item)
            item_for_cluster["clean_text"] = clean_text
            cluster_id, _is_new = assign_to_cluster(
                conn,
                item_for_cluster,
                item["source_type"],
                title_threshold=settings.title_similarity_threshold,
                candidates=cluster_candidates,
                defer_commit=True,
            )
            priority = SOURCE_TYPE_PRIORITY.get(item["source_type"] or "", 40)
            if result.status == "LOW_PRIORITY":
                priority += 5
            # 신선도 정렬용 원문 발행시각 (0021). 미래 표기는 현재로 클램프해
            # 조간 창에서 빠지는 것을 막는다.
            enqueue_article_job(
                conn,
                cluster_id,
                priority,
                clamp_source_time(item.get("published_at"), datetime.now(timezone.utc)),
            )
            # 건당 1초 미만이라 int()로 누적하면 전부 0이 된다 — 실수로 모아
            # 마지막에 정수화한다 (운영 화면 클러스터 시간이 0으로 보이던 버그)
            cluster_elapsed += time.monotonic() - cluster_start

        since_commit += 1
        if since_commit >= COMMIT_EVERY:
            conn.commit()
            since_commit = 0
    conn.commit()  # 잔여분 (배칭 커밋 후 남은 것)
    usage.record_stage("cluster", cluster_elapsed)
    usage.record_stage(
        "normalize", time.monotonic() - normalize_start - cluster_elapsed
    )


def main() -> int:
    settings = Settings.from_env()
    settings.require("supabase_db_url")

    conn = connect(settings.supabase_db_url)
    settings.apply_app_settings(load_app_settings(conn))

    # 검색 키워드(네이버·구글) → sources 관리행 동기화 (2026-09-08).
    # due 수집원을 고르기 전에 해야 새 검색어가 이번 배치부터 돈다.
    # 동기화 실패가 수집을 막지는 않는다 — 기존 수집원으로 그대로 진행한다.
    try:
        from .search_keywords import sync_news_search_sources

        kw_stats = sync_news_search_sources(conn)
        print(
            f"[collect] 검색 키워드 동기화: 생성 {kw_stats['created']} · "
            f"갱신 {kw_stats['updated']} · 비활성 {kw_stats['deactivated']}"
        )
    except Exception as e:  # noqa: BLE001 — 동기화 실패가 수집을 막지 않는다
        conn.rollback()
        print(
            f"[collect] 검색 키워드 동기화 실패(기존 수집원으로 계속): {e}",
            file=sys.stderr,
        )

    # 강제 종료로 RUNNING에 굳은 이전 실행 기록 정리
    stale = close_stale_runs(conn)
    if stale:
        print(f"[collect] 중단된 실행 기록 {stale}건 정리")
    usage = UsageRecorder(conn, "collect")

    status = "SUCCESS"
    try:
        # 수집 대상 선정 (외부 리뷰 2차):
        # - fetch_interval_minutes가 지난 수집원만
        # - 가장 오래 성공하지 못한 수집원 우선 (뒷순위 기아 방지)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM sources
                WHERE is_active = true
                  AND (last_success_at IS NULL
                       OR last_success_at < now() - make_interval(mins => fetch_interval_minutes))
                ORDER BY last_success_at ASC NULLS FIRST, priority ASC
                """
            )
            sources = cur.fetchall()

        # fetch와 후처리 예산을 분리 — fetch가 오래 걸려도
        # 본문 추출·클러스터·큐 등록 시간을 보장한다 (외부 리뷰 2차)
        fetch_deadline = time.monotonic() + settings.fetch_budget_seconds

        fetch_start = time.monotonic()
        total_new = 0
        for source in sources:
            if time.monotonic() > fetch_deadline:
                print(f"[collect] fetch 예산 소진 — {source['name']} 이후 수집원은 다음 배치로")
                break
            _, new = run_source(conn, source, settings, fetch_deadline)
            total_new += new
        usage.record_stage("fetch", time.monotonic() - fetch_start)
        usage.counts["collected"] = total_new

        process_deadline = time.monotonic() + settings.process_budget_seconds
        process_new_items(conn, settings, usage, process_deadline)
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        status = "FAILURE"
        print(f"[collect] 배치 실패: {e}", file=sys.stderr)
    finally:
        pending, oldest = count_pending(conn)
        usage.finish(status, remaining_pending=pending, oldest_pending_minutes=oldest)
        conn.close()
    return 0 if status == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
