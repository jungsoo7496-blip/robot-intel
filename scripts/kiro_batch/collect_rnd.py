"""NTIS 국가R&D통합공고 수집 (사용자 요청 2026-08-07/08 — NTIS만 사용).

NTIS(ntis.go.kr) 국가R&D통합공고에서 접수 중·접수 예정 공고를 수집해
rnd_announcements에 upsert한다. 뉴스 파이프라인과 분리된 경량 수집 —
본문 추출 파이프라인·클러스터·Gemini 분석 없음.

- 공식 RSS(rss.do)는 로그인 회원 전용이라, 같은 데이터가 나오는 공개
  목록(rndgate/eg/un/ra/mng.do)을 직접 조회한다.
- 목록: 공고명·부처명·공고일·마감일·D-day·공고UID.
- 상세(view.do): 접수기간·공고기관·공고유형·공고금액·본문 — 새 공고만
  1회 조회해 저장한다 (재수집 시 상세 재요청 없음).
- 로봇 관련 여부는 제목+본문 키워드 매칭 (recall 우선 — 사용자 지시).

실행: python -m kiro_batch.collect_rnd  (collect 워크플로의 추가 스텝)
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from .config import Settings
from .db import connect
from .keyword_rules import Rules, Term, compile_terms, load_rules, plain, regex

NTIS_LIST_URL = "https://www.ntis.go.kr/rndgate/eg/un/ra/mng.do"
NTIS_VIEW_URL = "https://www.ntis.go.kr/rndgate/eg/un/ra/view.do?roRndUid="

PAGE_UNIT = 100
MAX_PAGES = 12
DETAIL_INTERVAL_SECONDS = 0.5  # 상세 조회 예의 간격
DESCRIPTION_MAX = 700

# 로봇 관련 키워드 (사용자: "로봇 관련이면 다" — 제목+본문에 폭넓게 적용)
# 2026-08-09 확장: 내부 검토를 반영해 인접 분야(AI 제조·공정지능화, 재활·의료·돌봄,
# 소방·재난, 스마트팜·농기계, 해양·무인장비, 디지털트윈·모빌리티)까지 넓혔다.
ROBOT_KEYWORDS = [
    "로봇",
    "로보틱스",
    "자동화",
    "무인",
    "드론",
    "자율",  # 자율주행·자율운항·자율제조·자율행동체·자율적재·자율용접 등 포괄
    "피지컬 ai",
    "피지컬ai",
    "physical ai",
    "phisical ai",  # 공고 오탈자 실사례
    "스마트공장",
    "스마트 공장",
    "스마트팩토리",
    "스마트제조",
    "스마트 제조",
    "amr",
    "agv",
    "협동로봇",
    "휴머노이드",
    "머신비전",
    "웨어러블",
    "외골격",
    "엑소수트",
    "근력보조",
    "착용형",
    "매니퓰레이터",
    "그리퍼",
    "액추에이터",
    # AI 제조·공정 지능화
    "인공지능",
    "지능형",
    "지능화",
    "디지털트윈",
    "디지털 트윈",
    "예지보전",
    "머신러닝",
    "제조데이터",
    "제조 데이터",
    "공정혁신",
    "용접",
    "품질검사",
    "품질 검사",
    # 재활·의료·돌봄
    "재활",
    "의료기기",
    "돌봄",
    "요양",
    # 소방·재난 (소방청 과제 다수)
    "소방",
    "화재",
    "재난",
    "구조장비",
    # 농업·스마트팜
    "스마트팜",
    "농기계",
    "파종기",
    "방제",
    # 해양·수중
    "수중",
    "수륙양용",
    "무인선",
    "무인수상정",
    "무인잠수정",
    # 이동체
    "모빌리티",
    # 로봇 학습용 데이터
    "모션 데이터",
    "모션데이터",
    "모션캡처",
    "모션캡쳐",
]

# 단어 경계가 필요한 짧은 영문 약어 — 'ai'를 부분일치로 하면 chain·maintain
# 등에 오탐한다. \b는 한글을 단어문자로 봐서 "AI기반"(붙여쓰기)을 놓치므로
# 영숫자 경계만 검사한다 (제목·본문 소문자 기준).
def _abbr(word: str) -> str:
    return rf"(?<![a-z0-9]){word}(?![a-z0-9])"


ROBOT_KEYWORD_PATTERNS = [
    _abbr("ai"),   # AI 기반·AI기반·Edge AI·AI팩토리 등
    _abbr("ax"),   # 제조AX·공정혁신(AX)
    _abbr("ugv"),
    _abbr("uav"),
    _abbr("pdm"),  # 예지보전
]

# 실제 판정에 쓰는 매처 (2026-09-08, 관리 기능). 위 상수는 내장 기본값이고,
# 운영에서는 main()이 configure_from_db(conn)로 keyword_rules
# (rule_set='rnd_keywords', kind=keyword|pattern)로 교체한다.
_terms: list[Term] = compile_terms(
    [*plain(ROBOT_KEYWORDS), *regex(ROBOT_KEYWORD_PATTERNS)]
)
_terms_source = "내장 기본값"


def configure(rules: Rules | None) -> bool:
    """{keyword: [...], pattern: [...]} 규칙으로 교체. keyword가 비어 있으면
    (None·0건) 내장 기본값으로 되돌리고 False."""
    global _terms, _terms_source
    if not rules or not rules.get("keyword"):
        _terms = compile_terms(
            [*plain(ROBOT_KEYWORDS), *regex(ROBOT_KEYWORD_PATTERNS)]
        )
        _terms_source = "내장 기본값"
        return False
    _terms = compile_terms(
        [*rules["keyword"], *rules.get("pattern", [])], label="rnd_keywords"
    )
    _terms_source = "DB"
    return True


def configure_from_db(conn) -> bool:
    """keyword_rules(rnd_keywords)로 교체. 실패·0건이면 기본값 유지 (배치는 계속)."""
    ok = configure(load_rules(conn, "rnd_keywords"))
    print(f"[collect_rnd] 로봇 키워드 출처 {_terms_source}: {len(_terms)}건")
    return ok


def reset() -> None:
    """내장 기본값으로 되돌린다 (테스트용)."""
    configure(None)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; KIRO-RobotIntel/0.1)"}

_KST = ZoneInfo("Asia/Seoul")


def kst_date(now_utc: datetime | None = None) -> date:
    """KST 기준 오늘 날짜 (외부 리뷰 P2-3 — runner는 UTC라 date.today()면
    한국 자정~오전 9시 사이 접수중/예정 판정이 하루 어긋난다). 순수 계산."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(_KST).date()

# 목록 항목 앵커 = fn_view('공고UID') 링크. 제목 뒤 텍스트에 부처·날짜·D-day.
_ITEM_RE = re.compile(
    r"fn_view\('(?P<uid>\d+)'\)[^>]*>(?P<title>.*?)</a>(?P<rest>.*?)"
    r"(?=fn_view\('\d+'\)|\Z)",
    re.S,
)
_DATE_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")
_REST_WINDOW = 1500


@dataclass
class Announcement:
    agency: str | None
    title: str
    uid: str
    source_url: str
    posted_date: date | None
    deadline_date: date | None
    d_day_label: str
    # 상세에서 채우는 필드 (이미 저장된 공고는 조회 생략 — None 유지)
    apply_start: date | None = None
    notice_type: str | None = None
    budget_text: str | None = None
    org_name: str | None = None
    description: str | None = None
    matched_keywords: list[str] = field(default_factory=list)


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def match_keywords(*texts: str | None) -> list[str]:
    low = " ".join(t for t in texts if t).lower()
    hits: set[str] = set()
    for term in _terms:
        m = term.find(low)
        if m:
            hits.add(m)
    return sorted(hits)


def parse_page(html: str) -> list[Announcement]:
    out: list[Announcement] = []
    seen: set[str] = set()
    for m in _ITEM_RE.finditer(html):
        uid = m.group("uid")
        if uid in seen:
            continue
        title = _clean(m.group("title"))
        rest_text = _clean(m.group("rest")[:_REST_WINDOW])
        if not title:
            continue
        seen.add(uid)

        dates = [
            date(int(y), int(mo), int(d))
            for y, mo, d in _DATE_RE.findall(rest_text)
        ]
        agency = None
        dm = _DATE_RE.search(rest_text)
        if dm:
            agency = rest_text[: dm.start()].strip() or None
        dday = re.search(r"D\s*-\s*\d+|마감|상시", rest_text)

        out.append(
            Announcement(
                agency=agency,
                title=title,
                uid=uid,
                source_url=f"{NTIS_VIEW_URL}{uid}",
                posted_date=dates[0] if dates else None,
                deadline_date=dates[1] if len(dates) > 1 else None,
                d_day_label=re.sub(r"\s", "", dday.group(0)) if dday else "",
            )
        )
    return out


def _detail_field(text: str, label: str) -> str | None:
    m = re.search(rf"{label}\s*:\s*\|?\s*([^|]+)", text)
    if not m:
        return None
    value = m.group(1).strip()
    return value if value and value != "관련정보없음" else None


def parse_detail(html: str) -> dict:
    """상세 페이지에서 접수기간·기관·유형·금액·본문을 추출한다."""
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", "|", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\|+", "|", text)

    def to_date(raw: str | None) -> date | None:
        if not raw:
            return None
        dm = _DATE_RE.search(raw)
        if not dm:
            return None
        return date(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)))

    budget = _detail_field(text, "공고금액")
    if budget:
        digits = re.sub(r"[^\d]", "", budget)
        if not digits or int(digits) == 0:
            budget = None  # "0 억원"은 정보 없음으로 취급

    description = None
    i = text.find("공고내용")
    if i >= 0:
        body = text[i + len("공고내용"):].replace("|", " ")
        body = re.sub(r"\s+", " ", body).strip()
        if body:
            description = body[:DESCRIPTION_MAX]

    return {
        "apply_start": to_date(_detail_field(text, "접수일")),
        "deadline": to_date(_detail_field(text, "마감일")),
        "notice_type": _detail_field(text, "공고유형"),
        "budget_text": budget,
        "org_name": _detail_field(text, "공고기관명"),
        "agency": _detail_field(text, "부처명"),
        "description": description,
    }


def fetch_announcements(
    client: httpx.Client, status: str | None, max_pages: int
) -> list[Announcement]:
    items: list[Announcement] = []
    for page in range(1, max_pages + 1):
        data = {"pageIndex": str(page), "pageUnit": str(PAGE_UNIT)}
        if status:
            # 접수중(B)·접수예정(P) — 화면 필터 체크박스 값. 미지정 = 전체(마감 포함)
            data["searchStatusList"] = status
        resp = client.post(NTIS_LIST_URL, data=data)
        resp.raise_for_status()
        batch = parse_page(resp.text)
        items.extend(batch)
        if len(batch) < PAGE_UNIT:
            break
    return items


RECENT_PAGES = 3  # 마감 포함 최신 공고 (아카이브·재공고 예측용)


def enrich_new_details(conn, client: httpx.Client, items: list[Announcement]) -> int:
    """상세 미보유(신규) 공고만 상세 페이지를 1회 조회해 채운다."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_url FROM rnd_announcements WHERE description IS NOT NULL"
        )
        have = {r["source_url"] for r in cur.fetchall()}

    fetched = 0
    for a in items:
        if a.source_url in have:
            continue
        try:
            resp = client.get(a.source_url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            print(f"[collect_rnd] 상세 조회 실패({a.uid}): {e}", file=sys.stderr)
            continue
        d = parse_detail(resp.text)
        a.apply_start = d["apply_start"]
        a.deadline_date = d["deadline"] or a.deadline_date
        a.notice_type = d["notice_type"]
        a.budget_text = d["budget_text"]
        a.org_name = d["org_name"]
        a.agency = d["agency"] or a.agency
        a.description = d["description"]
        fetched += 1
        time.sleep(DETAIL_INTERVAL_SECONDS)

    for a in items:
        a.matched_keywords = match_keywords(a.title, a.description)
    return fetched


def save(conn, items: list[Announcement], today: date) -> tuple[int, int]:
    new_count = 0
    with conn.cursor() as cur:
        for a in items:
            status_label = (
                "접수예정" if a.apply_start and a.apply_start > today else "접수중"
            )
            cur.execute(
                """
                INSERT INTO rnd_announcements
                  (board, agency, title, source_url, posted_date, deadline_date,
                   d_day_label, apply_start, notice_type, budget_text, org_name,
                   description, status_label, is_robot_related, matched_keywords)
                VALUES ('국가', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_url) DO UPDATE SET
                  d_day_label = EXCLUDED.d_day_label,
                  deadline_date = coalesce(EXCLUDED.deadline_date,
                                           rnd_announcements.deadline_date),
                  apply_start = coalesce(EXCLUDED.apply_start,
                                         rnd_announcements.apply_start),
                  notice_type = coalesce(EXCLUDED.notice_type,
                                         rnd_announcements.notice_type),
                  budget_text = coalesce(EXCLUDED.budget_text,
                                         rnd_announcements.budget_text),
                  org_name = coalesce(EXCLUDED.org_name,
                                      rnd_announcements.org_name),
                  description = coalesce(EXCLUDED.description,
                                         rnd_announcements.description),
                  status_label = EXCLUDED.status_label,
                  is_robot_related = rnd_announcements.is_robot_related
                                     OR EXCLUDED.is_robot_related,
                  matched_keywords = CASE
                    WHEN cardinality(EXCLUDED.matched_keywords) >
                         cardinality(rnd_announcements.matched_keywords)
                    THEN EXCLUDED.matched_keywords
                    ELSE rnd_announcements.matched_keywords END,
                  last_seen_at = now()
                RETURNING (xmax = 0) AS inserted
                """,
                (
                    a.agency,
                    a.title,
                    a.source_url,
                    a.posted_date,
                    a.deadline_date,
                    a.d_day_label,
                    a.apply_start,
                    a.notice_type,
                    a.budget_text,
                    a.org_name,
                    a.description,
                    status_label,
                    bool(a.matched_keywords),
                    a.matched_keywords,
                ),
            )
            if cur.fetchone()["inserted"]:
                new_count += 1
    conn.commit()
    return len(items), new_count


def main() -> int:
    settings = Settings.from_env()
    settings.require("supabase_db_url")
    today = kst_date()

    conn = connect(settings.supabase_db_url)
    try:
        configure_from_db(conn)  # 키워드 규칙 (DB 실패 시 내장 기본값)
        with httpx.Client(
            headers=_HEADERS, timeout=30.0, follow_redirects=True
        ) as client:
            try:
                open_items = fetch_announcements(client, "P,B", MAX_PAGES)
                recent_items = fetch_announcements(client, None, RECENT_PAGES)
            except httpx.HTTPError as e:
                print(f"[collect_rnd] NTIS 목록 조회 실패: {e}", file=sys.stderr)
                return 1

            # 접수중·예정 = 전량 상세 조회. 마감 포함 최신 공고는 제목이
            # 로봇 관련일 때만 상세 조회 (요청량 절제 — 나머지는 목록 필드만)
            open_uids = {a.uid for a in open_items}
            recent_extra = [
                a
                for a in recent_items
                if a.uid not in open_uids and match_keywords(a.title)
            ]
            items = open_items + recent_extra
            details = enrich_new_details(conn, client, items)

        total, new = save(conn, items, today)
        robot = sum(1 for a in items if a.matched_keywords)
        print(
            f"[collect_rnd] 공고 {total}건 (신규 {new}, 상세 조회 {details}) — "
            f"로봇 관련 {robot}건"
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
