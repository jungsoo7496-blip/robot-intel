"""파일 금고(vault) — 무거운 부속을 반달(半月) 단위 파일로 옮기고 DB에서 지운다 (2026-09-17).

목적: Supabase 무료 DB(500 MB)를 넘기지 않으면서 기사를 하나도 안 지운다.
화면이 실제로 그리는 '카드'(published_items·content_clusters·cluster_members·
policy_details)는 DB에 영구히 남기고, 무거운 부속(그 클러스터의 analyses 전부·끝난
analysis_jobs·raw_items 본문)은 기간 파일로 Storage 비공개 버킷 'vault'에 올린 뒤
DB에서 지운다. raw_items 행과 cluster_members 행은 남긴다 — canonical_url UNIQUE가
재수집을 막고, cluster_members가 남아야 cleanup의 '만료 행 삭제'가 구성원 raw_items를
지우지 않는다(C4).

기간(period) 키 = published_items.published_at 을 Asia/Seoul로 바꾼 반달(C1):
  'YYYY-MM-a' = 1~15일, 'YYYY-MM-b' = 16일~말일.
  (무료 Storage 파일 상한 50 MB — 월 1파일이면 2~3개월 안에 넘는다.)
  vault_manifest.month 컬럼에 이 문자열이 그대로 들어간다 (CHECK '^\\d{4}-\\d{2}-[ab]$').
파일:
  items/YYYY-MM-a.jsonl.gz  레코드 1건 = 독립된 gzip 멤버 1개를 이어 붙인 것.
                            전체를 gunzip하면 JSONL, 각 멤버는 바이트 구간만 받아
                            단독으로 gunzip 가능 (화면이 Range 요청으로 1건씩 읽는다)
  items/YYYY-MM-a.idx.json  {"<published_item_id>": [offset, length], ...}
레코드(JSON 한 줄, UTF-8, 키 순서 고정 — RECORD_KEYS):
  {"id","cluster_id","published_at","display_date","is_visible","title",
   "analysis": current analyses 행 전체|null,
   "analysis_history": current가 아닌 그 클러스터의 analyses 행 전부(generated_at 순) — C2,
   "related_sources": [...], "body": 대표 본문|null, "policy": policy_details 행 전체|null}
기간 파일은 그 기간 published_items 전부(is_visible 무관)를 담는다.
manifest.analysis_ids = {"<published_item_id>": ["<analysis id>", ...]} — 파일에 들어간
분석 id 전부(current 포함). prune은 이 목록에 있는 id만 지운다(C3).

단계 (cleanup.py 일일 계획 — 순서 고정):
  a) export  기한이 찬 기간(기간 마지막 날(KST) + vault_window_days 경과, 오늘이 속한 기간
             제외, manifest에 없거나 storage_verified_at 없음, verify_failures < 3)을 임시
             파일로 만들어 Storage에 올리고 manifest upsert
  b) verify  같은 실행에서 방금 올린 파일을 다시 내려받아 sha256·바이트·행 수를
             manifest와, 무작위 20건을 Range 요청 → gunzip → id·title·published_at을
             DB와 대조. 통과 시 storage_verified_at(verify_failures=0). 실패면 기록만
             (stderr + operation_events VAULT_VERIFY_FAILED) 남기고 verify_failures+1;
             3에 이르면 VAULT_VERIFY_STALLED를 1회 기록하고 운영자가 0으로 되돌릴 때까지
             그 기간은 재시도하지 않는다(C9)
  c) prune   vault_prune_enabled='true' 이고 storage_verified_at·release_verified_at
             둘 다 있고 pruned_at 없는 기간만. 카드 행을 FOR UPDATE SKIP LOCKED로 잠근
             200건 배치. 카드의 current_analysis_id가 manifest.analysis_ids에 없으면
             (내보낸 뒤 재분석됨) 그 카드는 통째로 건너뛰고 VAULT_MISMATCH에 사유 기록.
             나머지는 vaulted_at=now() → 구성원 raw_items 본문 NULL(다른 미금고 카드의
             대표 본문은 보호, C5) → 기록된 analyses 삭제 → 끝난 analysis_jobs 삭제.
             살아 있는 분석 job(ACTIVE_JOB_STATUSES)이 걸린 클러스터는 건너뛴다 — 후보
             조회와 각 문장 안에서 다시 확인. 실행당 상한 vault_prune_max_per_run
  d) selfcheck 매일 manifest와 실제 vaulted_at 카드 수, Storage 파일 존재(HEAD) 대조 →
             VAULT_MISMATCH. storage_verified_at 뒤 14일 넘게 release_verified_at이 없는
             기간 → VAULT_MIRROR_STALE(C10)

release_verified_at은 옛 비공개 저장소(archive)의 backup.yml 미러 단계가 채운다.
환경변수: SUPABASE_DB_URL, NEXT_PUBLIC_SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY.

구조: 기간 키·파일 형식(순수 함수) / VaultStorage(HTTP) / VaultDB(SQL) / run_* (조합).
파일 형식 왕복은 HTTP·DB 없이 테스트한다 (tests/unit/test_vault.py).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import random
import re
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import psycopg

from .config import Settings

KST = ZoneInfo("Asia/Seoul")
BUCKET = "vault"

RECORD_KEYS = (
    "id", "cluster_id", "published_at", "display_date", "is_visible", "title",
    "analysis", "analysis_history", "related_sources", "body", "policy",
)
RELATED_SOURCE_KEYS = ("is_representative", "title", "url", "published_at", "source_name")

# verify 표본 — 무작위 20건을 Range로 1건씩 받아 DB와 대조한다
SAMPLE_SIZE = 20
# export의 DB 페이지 크기(keyset). 한 페이지가 db.connect의 statement_timeout 60초 안에 끝나야 한다
EXPORT_PAGE_ROWS = 500
# 실행당 내보낼 최대 기간 수 — 첫 실행의 적체(여러 기간)를 cleanup.yml timeout 안에 나눠 처리
MAX_PERIODS_PER_RUN = 3
# prune 배치 크기 (설계 4c). 배치마다 커밋 — 중간에 죽어도 그때까지는 정합
PRUNE_BATCH_ROWS = 200
DEFAULT_PRUNE_MAX_PER_RUN = 3000
# C8 — vault_window_days 하한·기본값. publish.MERGE_WINDOW_DAYS(7)의 2배: 2차 병합이
# 이미 게시된 카드에 새 기사를 흡수하고 재분석을 큐에 넣는 창이 완전히 닫힌 뒤여야
# '내보낸 뒤 재분석됨' 불일치가 생기지 않는다
MIN_WINDOW_DAYS = 14
DEFAULT_WINDOW_DAYS = 14
# C9 — 검증 실패가 이 횟수에 이르면 그 기간은 운영자가 verify_failures를 0으로 되돌릴 때까지 쉰다
VERIFY_STALL_LIMIT = 3
# C10 — Storage 검증 뒤 이 일수 넘게 Release 미러가 없으면 VAULT_MIRROR_STALE
MIRROR_STALE_DAYS = 14
# 지워도 되는 job — 살아 있는 job이 하나라도 있는 클러스터는 건너뛴다
FINISHED_JOB_STATUSES = ("DONE", "CANCELLED", "FAILED")
ACTIVE_JOB_STATUSES = ("PENDING", "PROCESSING", "RETRY", "DEFERRED")

NIL_UUID = "00000000-0000-0000-0000-000000000000"

PERIOD_RE = re.compile(r"^(\d{4})-(\d{2})-([ab])$")
# SQL에서 같은 기간 키를 만드는 식 (published_items.published_at 기준)
PERIOD_SQL = (
    "to_char(published_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM') || "
    "CASE WHEN extract(day FROM published_at AT TIME ZONE 'Asia/Seoul') <= 15 "
    "THEN '-a' ELSE '-b' END"
)


class VaultError(RuntimeError):
    """금고 단계 실패 — cleanup.run_plan이 작업별로 격리해 note에 남긴다."""


# ------------------------------------------------------------
# 기간 키 (Asia/Seoul 반달) — 순수 함수
# ------------------------------------------------------------
def period_of_date(day: date) -> str:
    """KST 날짜 → 'YYYY-MM-a'(1~15일) | 'YYYY-MM-b'(16일~말일)."""
    return f"{day:%Y-%m}-{'a' if day.day <= 15 else 'b'}"


def period_key(published_at: datetime) -> str:
    """published_at → 반달 기간 키 (Asia/Seoul). naive면 UTC로 본다."""
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return period_of_date(published_at.astimezone(KST).date())


def _parse_period(period: str) -> tuple[int, int, str]:
    m = PERIOD_RE.fullmatch(period) if isinstance(period, str) else None
    if m is None:
        raise VaultError(f"기간 키 형식이 아닙니다: {period!r} (YYYY-MM-a | YYYY-MM-b)")
    year, mon, half = int(m.group(1)), int(m.group(2)), m.group(3)
    if not (1 <= mon <= 12):
        raise VaultError(f"기간 키 형식이 아닙니다: {period!r} (월은 01~12)")
    return year, mon, half


def period_bounds(period: str) -> tuple[datetime, datetime]:
    """기간 키 → [시작 00:00 KST, 끝 00:00 KST) — timestamptz 비교용.

    'YYYY-MM-a' → [1일, 16일), 'YYYY-MM-b' → [16일, 다음 달 1일).
    """
    year, mon, half = _parse_period(period)
    month_start = datetime(year, mon, 1, tzinfo=KST)
    mid = datetime(year, mon, 16, tzinfo=KST)
    next_month = (
        datetime(year + 1, 1, 1, tzinfo=KST) if mon == 12 else datetime(year, mon + 1, 1, tzinfo=KST)
    )
    return (month_start, mid) if half == "a" else (mid, next_month)


def period_last_day(period: str) -> date:
    """기간의 마지막 날(KST 날짜) — 'a'는 15일, 'b'는 말일."""
    _, end = period_bounds(period)
    return end.date() - timedelta(days=1)


def period_is_due(period: str, today: date, window_days: int) -> bool:
    """기간 마지막 날(KST) + window_days 가 지났는가. today는 KST 날짜.

    오늘이 속한 기간(과 미래)은 절대 아니다 — 키가 사전순으로 시간순이라 문자열 비교로 충분.
    """
    if period >= period_of_date(today):
        return False
    return today > period_last_day(period) + timedelta(days=max(window_days, 0))


def periods_due(
    candidates: Iterable[str], manifest: dict[str, dict], today: date, window_days: int
) -> list[str]:
    """export 대상 기간 — 기한이 찼고, manifest에 없거나 storage_verified_at이 없으며,
    verify_failures가 상한(VERIFY_STALL_LIMIT) 미만인 기간. 오래된 순."""
    due: list[str] = []
    for period in sorted(set(candidates)):
        if not period_is_due(period, today, window_days):
            continue
        row = manifest.get(period)
        if row is not None:
            if row.get("storage_verified_at") is not None:
                continue
            if int(row.get("verify_failures") or 0) >= VERIFY_STALL_LIMIT:
                continue  # C9 — 운영자가 verify_failures=0으로 되돌리면 재개
        due.append(period)
    return due


def effective_window_days(days: object) -> int:
    """C8 — vault_window_days 하한 14. 그보다 작으면 14로 올리고 경고한다."""
    try:
        value = int(days)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = DEFAULT_WINDOW_DAYS
    if value < MIN_WINDOW_DAYS:
        print(
            f"[vault] vault_window_days={value}는 하한 {MIN_WINDOW_DAYS}일보다 작아 "
            f"{MIN_WINDOW_DAYS}일로 올립니다 (병합 창 7일의 2배)",
            file=sys.stderr,
        )
        return MIN_WINDOW_DAYS
    return value


def items_path(period: str) -> str:
    return f"items/{period}.jsonl.gz"


def index_path(period: str) -> str:
    return f"items/{period}.idx.json"


# ------------------------------------------------------------
# 파일 형식 — 순수 함수 (HTTP·DB 없이 왕복 테스트)
# ------------------------------------------------------------
def _json_default(value: object) -> object:
    # 레코드는 DB에서 jsonb로 받아 이미 JSON 값이지만, 안전망으로 남긴다
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"JSON으로 바꿀 수 없는 값: {type(value).__name__}")


def normalize_record(raw: dict) -> dict:
    """키 순서 고정(RECORD_KEYS) + related_sources 각 항목 키 순서 고정. 없는 키는 null.
    analysis_history는 항상 배열(없으면 빈 배열)."""
    rec = {key: raw.get(key) for key in RECORD_KEYS}
    rec["id"] = str(rec["id"])
    rec["cluster_id"] = None if rec["cluster_id"] is None else str(rec["cluster_id"])
    rec["analysis_history"] = list(raw.get("analysis_history") or [])
    rec["related_sources"] = [
        {key: src.get(key) for key in RELATED_SOURCE_KEYS}
        for src in (raw.get("related_sources") or [])
    ]
    return rec


def record_analysis_ids(raw: dict) -> list[str]:
    """레코드에 담긴 분석 id 전부 — current(analysis) + analysis_history. manifest.analysis_ids용."""
    ids: list[str] = []
    current = raw.get("analysis")
    if isinstance(current, dict) and current.get("id") is not None:
        ids.append(str(current["id"]))
    for hist in raw.get("analysis_history") or []:
        if isinstance(hist, dict) and hist.get("id") is not None:
            ids.append(str(hist["id"]))
    return ids


def encode_record(raw: dict) -> bytes:
    """레코드 → JSON 한 줄(UTF-8, 개행 포함) → 독립된 gzip 멤버 1개."""
    line = json.dumps(
        normalize_record(raw), ensure_ascii=False, separators=(",", ":"), default=_json_default,
    ) + "\n"
    return gzip.compress(line.encode("utf-8"), mtime=0)


def decode_member(chunk: bytes) -> dict:
    """gzip 멤버 1개(idx 구간) → 레코드."""
    return json.loads(gzip.decompress(chunk).decode("utf-8"))


def read_member(data: bytes, offset: int, length: int) -> dict:
    return decode_member(data[offset:offset + length])


def iter_records(data: bytes) -> Iterator[dict]:
    """기간 파일 전체 → 레코드 순서대로 (gzip.decompress는 이어 붙인 멤버를 한 번에 푼다).

    바이트 b'\\n'으로 나눈다 — 본문 안의 U+2028 같은 유니코드 줄바꿈이 str.splitlines()에
    걸려 레코드가 쪼개지는 일이 없게(JSON 문자열 안에는 실제 개행이 올 수 없다).
    """
    for line in gzip.decompress(data).split(b"\n"):
        if line:
            yield json.loads(line.decode("utf-8"))


def count_rows(data: bytes) -> int:
    return gzip.decompress(data).count(b"\n")


@dataclass
class PeriodFile:
    idx: dict[str, list[int]]
    rows: int
    bytes: int
    sha256: str
    # {"<published_item_id>": ["<analysis id>", ...]} — current 포함 전부 (C2)
    analysis_ids: dict[str, list[str]] = field(default_factory=dict)


def write_period_file(records: Iterable[dict], out: BinaryIO) -> PeriodFile:
    """레코드들을 멤버 gzip으로 이어 쓰고 idx·행 수·바이트·sha256·분석 id 목록을 돌려준다."""
    idx: dict[str, list[int]] = {}
    analysis_ids: dict[str, list[str]] = {}
    digest = hashlib.sha256()
    offset = 0
    for raw in records:
        member = encode_record(raw)
        out.write(member)
        digest.update(member)
        rec_id = str(raw["id"])
        if rec_id in idx:
            raise VaultError(f"레코드 id 중복: {rec_id}")
        idx[rec_id] = [offset, len(member)]
        analysis_ids[rec_id] = record_analysis_ids(raw)
        offset += len(member)
    return PeriodFile(
        idx=idx, rows=len(idx), bytes=offset, sha256=digest.hexdigest(), analysis_ids=analysis_ids,
    )


def pick_sample_ids(ids: list[str], k: int = SAMPLE_SIZE) -> list[str]:
    return random.sample(list(ids), min(k, len(ids)))


def check_file(data: bytes, idx: dict, manifest: dict) -> list[str]:
    """내려받은 기간 파일·idx가 manifest와 맞는지 — 어긋난 점 목록(비면 통과)."""
    problems: list[str] = []
    if len(data) != int(manifest["bytes"]):
        problems.append(f"바이트 수 {len(data)} ≠ manifest {manifest['bytes']}")
    sha = hashlib.sha256(data).hexdigest()
    if sha != manifest["sha256"]:
        problems.append(f"sha256 불일치 ({sha[:12]}… ≠ {str(manifest['sha256'])[:12]}…)")
    try:
        rows = count_rows(data)
    except (OSError, EOFError) as e:
        problems.append(f"gunzip 실패: {e}")
        rows = -1
    if rows != int(manifest["rows"]):
        problems.append(f"행 수 {rows} ≠ manifest {manifest['rows']}")
    if len(idx) != int(manifest["rows"]):
        problems.append(f"idx 항목 {len(idx)} ≠ manifest {manifest['rows']}")
    # 구간이 빈틈·겹침 없이 파일 전체를 덮는지
    expected = 0
    for rec_id, span in sorted(idx.items(), key=lambda kv: kv[1][0]):
        if len(span) != 2 or span[0] != expected or span[1] <= 0:
            problems.append(f"idx 구간 어긋남: {rec_id} → {span} (기대 offset {expected})")
            break
        expected += span[1]
    else:
        if expected != len(data):
            problems.append(f"idx 구간 합 {expected} ≠ 파일 크기 {len(data)}")
    for sid in manifest.get("sample_ids") or []:
        if sid not in idx:
            problems.append(f"표본 {sid} 이 idx에 없음")
    return problems


def _parse_ts(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def compare_card(record: dict, card: dict) -> list[str]:
    """금고 레코드 vs DB 카드(id·title·published_at) 대조 — 어긋난 점 목록."""
    problems: list[str] = []
    rid = str(record.get("id"))
    if rid != str(card.get("id")):
        problems.append(f"{rid}: id 불일치 (DB {card.get('id')})")
    if record.get("title") != card.get("title"):
        problems.append(f"{rid}: title 불일치")
    rec_ts, card_ts = _parse_ts(record.get("published_at")), _parse_ts(card.get("published_at"))
    if rec_ts is None or card_ts is None or rec_ts != card_ts:
        problems.append(f"{rid}: published_at 불일치 ({record.get('published_at')} ≠ {card.get('published_at')})")
    return problems


def split_prune_batch(
    batch: list[dict], analysis_ids: dict[str, list[str]]
) -> tuple[list[dict], dict[str, dict]]:
    """C3 — 카드의 current_analysis_id가 manifest.analysis_ids에 기록돼 있는 카드만 지운다.

    반환 = (지울 카드, {건너뛴 카드 id: 사유 detail}). 기록에 없는 current(내보낸 뒤
    재분석됨)나 current가 없는 카드는 통째로 건너뛴다 — 본문 NULL·vaulted_at·삭제 전부 안 함.
    """
    ok: list[dict] = []
    skipped: dict[str, dict] = {}
    for card in batch:
        cid = str(card["id"])
        recorded = [str(a) for a in (analysis_ids.get(cid) or [])]
        current = card.get("current_analysis_id")
        current_s = None if current is None else str(current)
        if current_s is not None and current_s in recorded:
            ok.append(card)
            continue
        reason = (
            "current_analysis_id 없음" if current_s is None
            else "내보낸 뒤 재분석됨 — current_analysis_id가 금고 파일(manifest.analysis_ids)에 없다"
        )
        skipped[cid] = {
            "id": cid, "current_analysis_id": current_s, "recorded": recorded, "reason": reason,
        }
    return ok, skipped


def find_mismatches(manifest: dict[str, dict], stats: dict[str, dict]) -> list[str]:
    """selfcheck 순수 대조. stats = {period: {"total": 카드 수, "vaulted": vaulted_at 있는 카드 수}}."""
    problems: list[str] = []
    for period in sorted(stats):
        st = stats[period]
        if st.get("vaulted", 0) <= 0:
            continue
        row = manifest.get(period)
        if row is None or row.get("storage_verified_at") is None:
            problems.append(
                f"{period}: 검증된 금고 파일 없이 비워진 카드 {st['vaulted']}건 — 화면이 분석을 읽을 수 없다"
            )
    pruned_rows_sum = 0
    pruned_vaulted_sum = 0
    for period in sorted(manifest):
        row = manifest[period]
        st = stats.get(period, {"total": 0, "vaulted": 0})
        if int(st.get("total", 0)) != int(row["rows"]):
            problems.append(f"{period}: DB 카드 {st.get('total', 0)}건 ≠ manifest rows {row['rows']}")
        if row.get("pruned_at") is not None:
            pruned_rows_sum += int(row["rows"])
            pruned_vaulted_sum += int(st.get("vaulted", 0))
            if row.get("pruned_rows") != st.get("vaulted", 0):
                problems.append(
                    f"{period}: pruned_rows {row.get('pruned_rows')} ≠ vaulted_at 카드 {st.get('vaulted', 0)}건"
                )
    if pruned_rows_sum != pruned_vaulted_sum:
        problems.append(f"정리 완료 기간 합계: manifest rows {pruned_rows_sum} ≠ vaulted_at 카드 {pruned_vaulted_sum}")
    return problems


def find_mirror_stale(
    manifest: dict[str, dict], now: datetime, stale_days: int = MIRROR_STALE_DAYS
) -> list[str]:
    """C10 — storage_verified_at 뒤 stale_days 넘게 release_verified_at이 없는 기간."""
    stale: list[str] = []
    for period in sorted(manifest):
        row = manifest[period]
        verified = _parse_ts(row.get("storage_verified_at"))
        if verified is None or row.get("release_verified_at") is not None:
            continue
        age = now - verified
        if age > timedelta(days=stale_days):
            stale.append(
                f"{period}: Storage 검증 뒤 {age.days}일 — Release 미러(release_verified_at)가 아직 없다 "
                "(archive backup.yml 확인)"
            )
    return stale


# ------------------------------------------------------------
# Storage REST (httpx) — 테스트는 httpx.MockTransport 클라이언트를 넘긴다
# ------------------------------------------------------------
def _http_error(what: str, r: httpx.Response) -> VaultError:
    return VaultError(f"{what} 실패 HTTP {r.status_code}: {r.text[:200]}")


def auth_headers(service_role_key: str) -> dict[str, str]:
    """C6 — apikey는 항상, Authorization: Bearer는 JWT 형식 키일 때만.

    새 형식 키(sb_secret_…, sb_publishable_…)는 JWT가 아니라 Bearer로 보내면 Storage가
    거부한다. 화면(src/lib/vault.ts)·archive 미러도 같은 규칙.
    """
    headers = {"apikey": service_role_key}
    if not service_role_key.startswith(("sb_secret_", "sb_publishable_")):
        headers["Authorization"] = f"Bearer {service_role_key}"
    return headers


class VaultStorage:
    """Supabase Storage 비공개 버킷 'vault'. 인증은 service role 헤더."""

    def __init__(
        self, supabase_url: str, service_role_key: str, client: httpx.Client | None = None,
    ):
        if not supabase_url or not service_role_key:
            raise VaultError(
                "NEXT_PUBLIC_SUPABASE_URL·SUPABASE_SERVICE_ROLE_KEY가 없습니다 — "
                "cleanup.yml env 또는 scripts/.env를 확인하세요."
            )
        self.base = supabase_url.rstrip("/") + "/storage/v1"
        self._headers = auth_headers(service_role_key)
        self.client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0))

    def _headers_with(self, **extra: str) -> dict[str, str]:
        return {**self._headers, **extra}

    def _upload_url(self, path: str) -> str:
        return f"{self.base}/object/{BUCKET}/{path}"

    def _read_url(self, path: str) -> str:
        # 인증 다운로드 경로 — 화면(src/lib/vault.ts)·archive 미러도 같은 경로로 읽는다
        return f"{self.base}/object/authenticated/{BUCKET}/{path}"

    def ensure_bucket(self) -> None:
        """버킷이 없으면 만든다 (멱등)."""
        r = self.client.get(f"{self.base}/bucket/{BUCKET}", headers=self._headers_with())
        if r.status_code == 200:
            return
        if r.status_code not in (400, 404):
            raise _http_error("버킷 조회", r)
        r = self.client.post(
            f"{self.base}/bucket",
            headers=self._headers_with(),
            json={"id": BUCKET, "name": BUCKET, "public": False},
        )
        if r.status_code in (200, 201):
            return
        if r.status_code in (400, 409) and "exist" in r.text.lower():
            return  # 동시 생성 경쟁 — 이미 있음
        raise _http_error("버킷 생성", r)

    def upload(self, path: str, data: bytes, content_type: str) -> None:
        r = self.client.post(
            self._upload_url(path),
            headers=self._headers_with(**{"x-upsert": "true", "Content-Type": content_type}),
            content=data,
        )
        if r.status_code not in (200, 201):
            raise _http_error(f"업로드 {path}", r)

    def download(self, path: str) -> bytes:
        r = self.client.get(self._read_url(path), headers=self._headers_with())
        if r.status_code != 200:
            raise _http_error(f"다운로드 {path}", r)
        return r.content

    def download_range(self, path: str, offset: int, length: int) -> bytes:
        """idx 구간 1개 — 화면이 하는 것과 같은 요청. 206이 아니면 실패로 본다."""
        r = self.client.get(
            self._read_url(path),
            headers=self._headers_with(Range=f"bytes={offset}-{offset + length - 1}"),
        )
        if r.status_code != 206:
            raise VaultError(f"Range 요청 {path} {offset}+{length}: HTTP {r.status_code} (206 기대)")
        if len(r.content) != length:
            raise VaultError(f"Range 응답 길이 {len(r.content)} ≠ {length} ({path} {offset})")
        return r.content

    def exists(self, path: str) -> bool:
        r = self.client.head(self._read_url(path), headers=self._headers_with())
        if r.status_code == 200:
            return True
        if r.status_code in (400, 404):
            return False
        raise _http_error(f"HEAD {path}", r)


def storage_from_settings(settings: Settings) -> VaultStorage:
    return VaultStorage(settings.supabase_url, settings.supabase_service_role_key)


# ------------------------------------------------------------
# DB 접근 — 한 곳에 모아 테스트에서 같은 메서드의 가짜로 바꾼다
# ------------------------------------------------------------
RECORD_PAGE_SQL = """
SELECT p.published_at AS _published_at, p.id::text AS _id,
       jsonb_build_object(
         'id', p.id, 'cluster_id', p.cluster_id, 'published_at', p.published_at,
         'display_date', p.display_date, 'is_visible', p.is_visible, 'title', p.title,
         'analysis', CASE WHEN a.id IS NULL THEN NULL ELSE to_jsonb(a) END,
         'analysis_history', coalesce((
            SELECT jsonb_agg(to_jsonb(h) ORDER BY h.generated_at, h.id)
            FROM analyses h
            WHERE h.cluster_id = p.cluster_id
              AND (p.current_analysis_id IS NULL OR h.id <> p.current_analysis_id)), '[]'::jsonb),
         'related_sources', coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                     'is_representative', cm.is_representative,
                     'title', ri.title, 'url', ri.url, 'published_at', ri.published_at,
                     'source_name', coalesce(s.name, '수동 등록'))
                   ORDER BY cm.is_representative DESC, ri.published_at)
            FROM cluster_members cm
            JOIN raw_items ri ON ri.id = cm.raw_item_id
            LEFT JOIN sources s ON s.id = ri.source_id
            WHERE cm.cluster_id = p.cluster_id), '[]'::jsonb),
         'body', rep.clean_text,
         'policy', CASE WHEN pd.published_item_id IS NULL THEN NULL ELSE to_jsonb(pd) END
       ) AS record
FROM published_items p
LEFT JOIN analyses a ON a.id = p.current_analysis_id
LEFT JOIN policy_details pd ON pd.published_item_id = p.id
LEFT JOIN content_clusters c ON c.id = p.cluster_id
LEFT JOIN raw_items rep ON rep.id = c.representative_raw_item_id
WHERE p.published_at >= %s AND p.published_at < %s
  AND (p.published_at, p.id) > (%s::timestamptz, %s::uuid)
ORDER BY p.published_at, p.id
LIMIT %s
"""

# prune 후보 — 카드 행을 잠근다(FOR UPDATE SKIP LOCKED). 같은 트랜잭션의 prune_batch가
# 처리하고 커밋해야 잠금이 풀린다. 살아 있는 job이 걸린 클러스터와 이번 실행에서
# 건너뛴 카드(current 불일치)는 제외
PRUNE_CANDIDATES_SQL = """
SELECT p.id::text AS id, p.cluster_id::text AS cluster_id,
       p.current_analysis_id::text AS current_analysis_id
FROM published_items p
WHERE p.published_at >= %s AND p.published_at < %s
  AND p.vaulted_at IS NULL
  AND NOT (p.id = ANY(%s::uuid[]))
  AND NOT EXISTS (
    SELECT 1 FROM analysis_jobs j
    WHERE j.cluster_id = p.cluster_id AND j.status = ANY(%s)
  )
ORDER BY p.published_at, p.id
LIMIT %s
FOR UPDATE OF p SKIP LOCKED
"""

# prune_batch ① — 표식이 먼저다. RETURNING으로 이번 배치의 대상이 확정되고, 뒤 문장들은
# 그 카드·클러스터만 건드린다. 살아 있는 job 조건을 문장 안에서 다시 확인한다 (경합 [29])
PRUNE_MARK_SQL = """
UPDATE published_items p SET vaulted_at = now()
WHERE p.id = ANY(%s::uuid[]) AND p.vaulted_at IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM analysis_jobs j
    WHERE j.cluster_id = p.cluster_id AND j.status = ANY(%s)
  )
RETURNING p.id::text AS id, p.cluster_id::text AS cluster_id
"""

# prune_batch ② — 구성원 본문 NULL (행은 남긴다 — canonical_url UNIQUE가 재수집을 막는다).
# C5: 그 raw_item이 다른 미금고 카드(vaulted_at IS NULL, 이번 배치 카드 제외)의 대표이면 보호
PRUNE_BODY_SQL = """
UPDATE raw_items ri SET clean_text = NULL, raw_text = NULL
WHERE ri.id IN (
  SELECT cm.raw_item_id FROM cluster_members cm WHERE cm.cluster_id = ANY(%s::uuid[])
  UNION
  SELECT c.representative_raw_item_id FROM content_clusters c
  WHERE c.id = ANY(%s::uuid[]) AND c.representative_raw_item_id IS NOT NULL
)
AND NOT EXISTS (
  SELECT 1 FROM content_clusters c2
  JOIN published_items p2 ON p2.cluster_id = c2.id
  WHERE c2.representative_raw_item_id = ri.id
    AND p2.vaulted_at IS NULL
    AND NOT (p2.id = ANY(%s::uuid[]))
)
AND NOT EXISTS (
  SELECT 1 FROM cluster_members cm3
  JOIN analysis_jobs j ON j.cluster_id = cm3.cluster_id
  WHERE cm3.raw_item_id = ri.id AND j.status = ANY(%s)
)
"""

# prune_batch ③ — C3: manifest.analysis_ids에 기록된 id만, 이번 배치 클러스터의 것만.
# FK ON DELETE SET NULL이 published_items.current_analysis_id를 비운다
PRUNE_ANALYSES_SQL = """
DELETE FROM analyses a
WHERE a.id = ANY(%s::uuid[]) AND a.cluster_id = ANY(%s::uuid[])
  AND NOT EXISTS (
    SELECT 1 FROM analysis_jobs j
    WHERE j.cluster_id = a.cluster_id AND j.status = ANY(%s)
  )
"""

# prune_batch ④ — 끝난 job만. cluster_members는 지우지 않는다 (C4)
PRUNE_JOBS_SQL = """
DELETE FROM analysis_jobs j
WHERE j.cluster_id = ANY(%s::uuid[]) AND j.status = ANY(%s)
  AND NOT EXISTS (
    SELECT 1 FROM analysis_jobs j2
    WHERE j2.cluster_id = j.cluster_id AND j2.status = ANY(%s)
  )
"""


class VaultDB:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def _one(self, sql: str, params: tuple = ()) -> dict:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def end_read(self) -> None:
        """읽기 트랜잭션 종료(잠금 해제) — 업로드·다운로드 동안 idle-in-transaction으로 끊기지 않게."""
        self.conn.commit()

    def require_ready(self) -> None:
        if self._one("SELECT to_regclass('public.vault_manifest') AS t")["t"] is None:
            self.conn.rollback()
            raise VaultError(
                "vault_manifest 표가 없습니다 — supabase/migrations/20260917000001_vault.sql을 먼저 적용하세요."
            )

    def period_candidates(self) -> list[str]:
        rows = self._all(
            f"SELECT DISTINCT {PERIOD_SQL} AS period FROM published_items ORDER BY 1"
        )
        return [r["period"] for r in rows]

    def manifest_rows(self) -> dict[str, dict]:
        return {r["month"]: dict(r) for r in self._all("SELECT * FROM vault_manifest")}

    def iter_period_records(self, start: datetime, end: datetime) -> Iterator[dict]:
        """그 기간 카드 전부(is_visible 무관)를 published_at, id 순으로 — keyset 페이지."""
        last_ts, last_id = start, NIL_UUID
        while True:
            rows = self._all(RECORD_PAGE_SQL, (start, end, last_ts, last_id, EXPORT_PAGE_ROWS))
            for r in rows:
                yield r["record"]
            if len(rows) < EXPORT_PAGE_ROWS:
                return
            last_ts, last_id = rows[-1]["_published_at"], rows[-1]["_id"]

    def period_counts(self, start: datetime, end: datetime) -> tuple[int, int]:
        """(그 기간 카드 수, 그중 vaulted_at 있는 수)."""
        r = self._one(
            """
            SELECT count(*) AS total, count(vaulted_at) AS vaulted
            FROM published_items WHERE published_at >= %s AND published_at < %s
            """,
            (start, end),
        )
        return int(r["total"]), int(r["vaulted"])

    def load_cards(self, ids: list[str]) -> dict[str, dict]:
        rows = self._all(
            "SELECT id::text AS id, title, published_at FROM published_items WHERE id = ANY(%s::uuid[])",
            (list(ids),),
        )
        return {r["id"]: dict(r) for r in rows}

    def upsert_manifest(self, row: dict) -> None:
        """export 직후 — 검증 표식은 비운다 (파일이 새로 만들어졌으니 다시 검증).
        verify_failures는 건드리지 않는다 — 재내보내기마다 0이 되면 C9 상한이 무의미해진다."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vault_manifest
                  (month, rows, bytes, sha256, storage_path, sample_ids, analysis_ids, exported_at,
                   storage_verified_at, release_verified_at, release_tag)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, now(), NULL, NULL, NULL)
                ON CONFLICT (month) DO UPDATE SET
                  rows = EXCLUDED.rows, bytes = EXCLUDED.bytes, sha256 = EXCLUDED.sha256,
                  storage_path = EXCLUDED.storage_path, sample_ids = EXCLUDED.sample_ids,
                  analysis_ids = EXCLUDED.analysis_ids,
                  exported_at = now(), storage_verified_at = NULL,
                  release_verified_at = NULL, release_tag = NULL
                """,
                (
                    row["month"], row["rows"], row["bytes"], row["sha256"], row["storage_path"],
                    json.dumps(row["sample_ids"]),
                    json.dumps(row.get("analysis_ids") or {}),
                ),
            )
        self.conn.commit()

    def mark_storage_verified(self, period: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE vault_manifest SET storage_verified_at = now(), verify_failures = 0 WHERE month = %s",
                (period,),
            )
        self.conn.commit()

    def bump_verify_failures(self, period: str) -> int:
        """C9 — 검증 실패 +1. 반환 = 누적 횟수 (manifest 행이 없으면 0)."""
        r = self._one(
            """
            UPDATE vault_manifest SET verify_failures = verify_failures + 1
            WHERE month = %s RETURNING verify_failures
            """,
            (period,),
        )
        self.conn.commit()
        return int(r["verify_failures"]) if r else 0

    def record_event(self, event_type: str, reason: str, detail: dict) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO operation_events (event_type, target_table, reason, detail)
                VALUES (%s, 'vault_manifest', %s, %s::jsonb)
                """,
                (event_type, reason[:500], json.dumps(detail, ensure_ascii=False, default=_json_default)),
            )
        self.conn.commit()

    # --- prune ---
    def prunable_periods(self) -> list[str]:
        rows = self._all(
            """
            SELECT month FROM vault_manifest
            WHERE storage_verified_at IS NOT NULL AND release_verified_at IS NOT NULL
              AND pruned_at IS NULL
            ORDER BY month
            """
        )
        return [r["month"] for r in rows]

    def prune_candidates(
        self, start: datetime, end: datetime, limit: int, exclude_ids: list[str] | None = None,
    ) -> list[dict]:
        """아직 안 옮긴 카드를 잠근다(FOR UPDATE SKIP LOCKED) — 살아 있는 분석 job이 걸린
        클러스터와 exclude_ids(이번 실행에서 건너뛴 카드)는 제외. 같은 트랜잭션에서
        prune_batch를 불러야 한다."""
        return self._all(
            PRUNE_CANDIDATES_SQL,
            (start, end, list(exclude_ids or []), list(ACTIVE_JOB_STATUSES), limit),
        )

    def prune_batch(self, ids: list[str], analysis_ids: list[str]) -> int:
        """한 배치를 한 트랜잭션으로 — 표식과 부속 삭제가 함께 남거나 함께 안 남는다.
        반환 = 실제로 vaulted_at을 찍은 카드 수(살아 있는 job이 새로 생긴 카드는 빠진다).

        순서: ① vaulted_at 표식(RETURNING으로 대상 확정) ② 구성원 본문 NULL(C5 보호)
             ③ 기록된 analyses만 삭제(C3) ④ 끝난 analysis_jobs 삭제. cluster_members는 남긴다(C4).
        """
        active = list(ACTIVE_JOB_STATUSES)
        with self.conn.cursor() as cur:
            cur.execute(PRUNE_MARK_SQL, (list(ids), active))
            marked = cur.fetchall()
            if not marked:
                self.conn.commit()
                return 0
            marked_ids = [r["id"] for r in marked]
            clusters = [r["cluster_id"] for r in marked]
            cur.execute(PRUNE_BODY_SQL, (clusters, clusters, marked_ids, active))
            cur.execute(PRUNE_ANALYSES_SQL, (list(analysis_ids), clusters, active))
            cur.execute(PRUNE_JOBS_SQL, (clusters, list(FINISHED_JOB_STATUSES), active))
        self.conn.commit()
        return len(marked)

    def finish_prune(self, period: str, start: datetime, end: datetime) -> int:
        r = self._one(
            """
            UPDATE vault_manifest
            SET pruned_at = now(),
                pruned_rows = (
                  SELECT count(*) FROM published_items
                  WHERE published_at >= %s AND published_at < %s AND vaulted_at IS NOT NULL
                )
            WHERE month = %s
            RETURNING pruned_rows
            """,
            (start, end, period),
        )
        self.conn.commit()
        return int(r["pruned_rows"]) if r else 0

    # --- selfcheck ---
    def period_stats(self) -> dict[str, dict]:
        rows = self._all(
            f"""
            SELECT {PERIOD_SQL} AS period,
                   count(*) AS total, count(vaulted_at) AS vaulted
            FROM published_items GROUP BY 1
            """
        )
        return {r["period"]: {"total": int(r["total"]), "vaulted": int(r["vaulted"])} for r in rows}


# ------------------------------------------------------------
# a) export + b) verify — 같은 실행에서
# ------------------------------------------------------------
def export_period(db: VaultDB, storage: VaultStorage, period: str) -> dict:
    """한 기간을 임시 파일로 만들어 올리고 manifest에 기록. 반환 = manifest 행(dict)."""
    start, end = period_bounds(period)
    total, vaulted = db.period_counts(start, end)
    if vaulted:
        db.end_read()
        raise VaultError(
            f"{period}: 이미 금고로 옮겨진 카드가 {vaulted}건 있어 다시 내보내지 않습니다 "
            "(덮어쓰면 분석 내용을 잃는다)"
        )
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="kiro-vault-") as tmp:
        items_file = Path(tmp) / f"{period}.jsonl.gz"
        with items_file.open("wb") as fh:
            pf = write_period_file(db.iter_period_records(start, end), fh)
        db.end_read()
        if pf.rows == 0:
            raise VaultError(f"{period}: 카드가 0건 — 후보 목록과 어긋남")
        if pf.rows != total:
            raise VaultError(f"{period}: 내보낸 {pf.rows}건 ≠ 카드 수 {total}건 — 내보내는 동안 바뀜")
        idx_file = Path(tmp) / f"{period}.idx.json"
        idx_file.write_text(json.dumps(pf.idx, separators=(",", ":")), encoding="utf-8")
        print(
            f"[vault] {period}: {pf.rows}건 → {pf.bytes / 1048576:.1f} MB "
            f"({time.monotonic() - started:.0f}초) 업로드 중"
        )
        storage.upload(items_path(period), items_file.read_bytes(), "application/gzip")
        storage.upload(index_path(period), idx_file.read_bytes(), "application/json")
    row = {
        "month": period,
        "rows": pf.rows,
        "bytes": pf.bytes,
        "sha256": pf.sha256,
        "storage_path": items_path(period),
        "sample_ids": pick_sample_ids(list(pf.idx)),
        "analysis_ids": pf.analysis_ids,
    }
    db.upsert_manifest(row)
    return row


def verify_period(db: VaultDB, storage: VaultStorage, period: str, row: dict) -> list[str]:
    """방금 올린 파일을 다시 내려받아 대조 — 어긋난 점 목록(비면 통과)."""
    data = storage.download(items_path(period))
    idx = json.loads(storage.download(index_path(period)).decode("utf-8"))
    problems = check_file(data, idx, row)
    if problems:
        return problems
    sample_ids = list(row.get("sample_ids") or [])
    cards = db.load_cards(sample_ids)
    db.end_read()
    for sid in sample_ids:
        offset, length = idx[sid]
        try:
            chunk = storage.download_range(items_path(period), offset, length)
            if chunk != data[offset:offset + length]:
                problems.append(f"{sid}: Range 응답이 파일 구간과 다름")
                continue
            rec = decode_member(chunk)
        except Exception as e:  # noqa: BLE001 — 표본 1건 실패도 전체 실패로 집계
            problems.append(f"{sid}: 구간 읽기 실패 — {e}")
            continue
        card = cards.get(sid)
        if card is None:
            problems.append(f"{sid}: DB에 카드 없음")
            continue
        problems += compare_card(rec, card)
    return problems


def run_export_verify(
    db: VaultDB,
    storage_factory: Callable[[], VaultStorage],
    window_days: int,
    today: date | None = None,
    max_periods: int = MAX_PERIODS_PER_RUN,
) -> int:
    """기한이 찬 기간을 내보내고 같은 실행에서 검증. 반환 = 검증 통과한 기간 수.

    한 기간이라도 검증에 실패하면 기록(stderr + operation_events + verify_failures+1)을
    남긴 뒤 VaultError를 낸다 — cleanup note에 실패로 보이게. 그 기간은 다음 실행이
    다시 시도한다 (verify_failures가 VERIFY_STALL_LIMIT에 이르면 쉰다 — C9).
    """
    db.require_ready()
    today = today or datetime.now(KST).date()
    due = periods_due(db.period_candidates(), db.manifest_rows(), today, window_days)
    db.end_read()
    if not due:
        print("[vault] 내보낼 기간 없음")
        return 0
    if len(due) > max_periods:
        print(f"[vault] 대상 {len(due)}개 기간 중 {max_periods}개만 이번 실행에서 (나머지는 다음 실행)")
        due = due[:max_periods]
    storage = storage_factory()
    storage.ensure_bucket()

    verified = 0
    failed: list[str] = []
    for period in due:
        try:
            row = export_period(db, storage, period)
            problems = verify_period(db, storage, period, row)
        except Exception as e:  # noqa: BLE001 — 기간별 격리
            db.conn.rollback()
            problems = [f"{type(e).__name__}: {e}"]
        if problems:
            failed.append(period)
            summary = " / ".join(problems)[:400]
            print(f"[vault] {period} 검증 실패 — {summary}", file=sys.stderr)
            db.record_event(
                "VAULT_VERIFY_FAILED",
                f"{period} 금고 파일 검증 실패 — {summary}",
                {"month": period, "problems": problems[:20]},
            )
            failures = db.bump_verify_failures(period)
            if failures >= VERIFY_STALL_LIMIT:
                msg = (
                    f"{period}: 검증 실패 {failures}회 — 운영자가 vault_manifest.verify_failures를 "
                    "0으로 되돌릴 때까지 이 기간은 재시도하지 않습니다"
                )
                print(f"[vault] {msg}", file=sys.stderr)
                db.record_event(
                    "VAULT_VERIFY_STALLED", msg, {"month": period, "verify_failures": failures},
                )
            continue
        db.mark_storage_verified(period)
        verified += 1
        print(f"[vault] {period}: 검증 통과 (표본 {len(row['sample_ids'])}건)")
    if failed:
        raise VaultError(f"검증 실패 {', '.join(failed)} (통과 {verified}건)")
    return verified


# ------------------------------------------------------------
# c) prune — 설정 스위치 뒤에서만
# ------------------------------------------------------------
def run_prune(db: VaultDB, enabled: bool, max_per_run: int) -> int:
    """검증(Storage·Release 둘 다)이 끝난 기간의 부속을 DB에서 지운다. 반환 = 이번에 옮긴 카드 수."""
    db.require_ready()
    if not enabled:
        print("[vault] prune 꺼짐 (vault_prune_enabled=false) — 내보내기·검증만")
        return 0
    limit = max(int(max_per_run), 0)
    periods = db.prunable_periods()
    db.end_read()
    if not periods:
        print("[vault] prune 대상 기간 없음 (Storage·Release 검증이 모두 끝난 기간이 없음)")
        return 0

    total = 0
    for period in periods:
        if total >= limit:
            print(f"[vault] 실행당 상한 {limit}건 도달 — {period} 이후는 다음 실행에서")
            break
        start, end = period_bounds(period)
        manifest = db.manifest_rows()[period]
        card_total, _ = db.period_counts(start, end)
        db.end_read()
        if card_total != int(manifest["rows"]):
            # 파일에 없는 카드가 생겼다(또는 사라졌다) — 비우면 그 카드의 분석을 잃는다
            msg = f"{period}: DB 카드 {card_total}건 ≠ manifest rows {manifest['rows']} — prune 건너뜀"
            print(f"[vault] {msg}", file=sys.stderr)
            db.record_event("VAULT_MISMATCH", msg, {"month": period, "stage": "prune"})
            continue
        recorded: dict[str, list[str]] = manifest.get("analysis_ids") or {}
        skipped: dict[str, dict] = {}
        while total < limit:
            batch = db.prune_candidates(
                start, end, min(PRUNE_BATCH_ROWS, limit - total), list(skipped),
            )
            if not batch:
                db.end_read()
                break
            ok, bad = split_prune_batch(batch, recorded)
            skipped.update(bad)
            if ok:
                ids = [card["id"] for card in ok]
                analysis_ids = sorted({aid for card in ok for aid in recorded.get(card["id"], [])})
                total += db.prune_batch(ids, analysis_ids)
            else:
                db.end_read()  # 잠금만 잡았으니 풀어 준다
            if len(batch) < PRUNE_BATCH_ROWS:
                break
        if skipped:
            msg = (
                f"{period}: 내보낸 뒤 재분석된 카드 {len(skipped)}건 건너뜀 — "
                "current_analysis_id가 manifest.analysis_ids에 없다 (본문·분석 그대로 둠)"
            )
            print(f"[vault] {msg}", file=sys.stderr)
            db.record_event(
                "VAULT_MISMATCH", msg,
                {"month": period, "stage": "prune", "skipped": list(skipped.values())[:50]},
            )
        _, vaulted = db.period_counts(start, end)
        remaining = card_total - vaulted
        if remaining == 0:
            pruned_rows = db.finish_prune(period, start, end)
            print(f"[vault] {period}: 정리 완료 — 카드 {pruned_rows}건의 부속을 DB에서 지웠다")
        else:
            db.end_read()
            print(
                f"[vault] {period}: 남은 카드 {remaining}건 "
                "(살아 있는 분석 job·재분석 불일치 또는 실행 상한) — 다음 실행에서 이어서"
            )
    return total


# ------------------------------------------------------------
# d) selfcheck — 매일
# ------------------------------------------------------------
def run_selfcheck(
    db: VaultDB, storage_factory: Callable[[], VaultStorage], now: datetime | None = None,
) -> int:
    """manifest ↔ 실제 카드 수 ↔ Storage 파일 존재 대조 + Release 미러 지연(C10).
    반환 = 어긋난 항목 수(0이 정상)."""
    db.require_ready()
    manifest = db.manifest_rows()
    stats = db.period_stats()
    db.end_read()
    problems = find_mismatches(manifest, stats)
    verified_periods = sorted(m for m, r in manifest.items() if r.get("storage_verified_at") is not None)
    if verified_periods:
        storage = storage_factory()
        for period in verified_periods:
            for path in (items_path(period), index_path(period)):
                try:
                    if not storage.exists(path):
                        problems.append(f"{period}: Storage에 {path} 없음")
                except Exception as e:  # noqa: BLE001 — 확인 실패도 어긋남으로 집계
                    problems.append(f"{period}: {path} 확인 실패 — {e}")
    if problems:
        summary = " / ".join(problems)[:400]
        print(f"[vault] 자가점검 어긋남 {len(problems)}건 — {summary}", file=sys.stderr)
        db.record_event(
            "VAULT_MISMATCH", f"금고 자가점검 어긋남 {len(problems)}건 — {summary}",
            {"problems": problems[:30]},
        )
    stale = find_mirror_stale(manifest, now or datetime.now(timezone.utc))
    if stale:
        summary = " / ".join(stale)[:400]
        print(f"[vault] Release 미러 지연 {len(stale)}건 — {summary}", file=sys.stderr)
        db.record_event(
            "VAULT_MIRROR_STALE", f"금고 Release 미러 지연 {len(stale)}건 — {summary}",
            {"problems": stale[:30], "stale_days": MIRROR_STALE_DAYS},
        )
    if not problems and not stale:
        print(f"[vault] 자가점검 이상 없음 (대장 {len(manifest)}개 기간)")
    return len(problems) + len(stale)


# ------------------------------------------------------------
# cleanup.py 진입점 — conn·settings만 받는다
# ------------------------------------------------------------
def export_and_verify(conn: psycopg.Connection, settings: Settings) -> int:
    return run_export_verify(
        VaultDB(conn),
        lambda: storage_from_settings(settings),
        effective_window_days(settings.vault_window_days),
    )


def prune(conn: psycopg.Connection, settings: Settings) -> int:
    return run_prune(VaultDB(conn), settings.vault_prune_enabled, settings.vault_prune_max_per_run)


def selfcheck(conn: psycopg.Connection, settings: Settings) -> int:
    return run_selfcheck(VaultDB(conn), lambda: storage_from_settings(settings))
