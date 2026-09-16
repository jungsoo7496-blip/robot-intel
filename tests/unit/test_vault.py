"""파일 금고(vault.py) — HTTP·DB 없이 파일 형식 왕복, 반달 기간 판정, 검증·정리 흐름 (2026-09-17).

- 기간 키: 반달 'YYYY-MM-a'(1~15일) | 'YYYY-MM-b'(16일~말일), Asia/Seoul 경계 (C1)
- 파일 형식: 멤버 gzip 연결 → idx → 구간 잘라 gunzip → 같은 레코드. analysis_history 포함 (C2)
- Storage 클라이언트: httpx.MockTransport로 경로·헤더 규칙(C6)·Range 206 고정
- export→verify / prune / selfcheck: 같은 메서드를 가진 가짜 DB(FakeDB)로 흐름 고정
  (기록 id만 삭제·current 불일치 건너뜀(C3), verify_failures 상한(C9), 미러 지연(C10))
- VaultDB SQL 술어: 실행된 SQL만 모으는 가짜 커넥션으로 검사
  (cluster_members 보존(C4), 대표 본문 보호(C5), FOR UPDATE SKIP LOCKED, 살아 있는 job 재확인)
- config·cleanup·publish(kiro_axes, C7)·generate_brief(금고 기간 재생성 거부)·마이그레이션 계약
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from kiro_batch import cleanup, generate_brief, publish, vault
from kiro_batch.config import Settings
from kiro_batch.publish import MERGE_WINDOW_DAYS
from kiro_batch.schemas import ArticleAnalysis
from kiro_batch.vault import (
    ACTIVE_JOB_STATUSES,
    FINISHED_JOB_STATUSES,
    MAX_PERIODS_PER_RUN,
    MIN_WINDOW_DAYS,
    MIRROR_STALE_DAYS,
    PRUNE_BATCH_ROWS,
    RECORD_KEYS,
    RELATED_SOURCE_KEYS,
    VERIFY_STALL_LIMIT,
    VaultDB,
    VaultError,
    VaultStorage,
    auth_headers,
    check_file,
    compare_card,
    decode_member,
    effective_window_days,
    encode_record,
    find_mirror_stale,
    find_mismatches,
    index_path,
    items_path,
    iter_records,
    normalize_record,
    period_bounds,
    period_is_due,
    period_key,
    period_last_day,
    period_of_date,
    periods_due,
    read_member,
    record_analysis_ids,
    run_export_verify,
    run_prune,
    run_selfcheck,
    split_prune_batch,
    write_period_file,
)

UTC = timezone.utc
SUPABASE_URL = "https://example.supabase.co"
KEY = "service-role-test-key"  # 옛 형식(JWT처럼 취급) — Bearer + apikey
NEW_KEY = "sb_secret_abcdef0123456789"  # 새 형식 — apikey만

MIGRATION = Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "20260917000001_vault.sql"


def make_record(i: int, period: str = "2026-07-a", cluster: str | None = None, **over) -> dict:
    """기간 키에 맞는 날짜(a=10일, b=20일 UTC → KST 같은 날)로 카드 레코드를 만든다."""
    yyyy_mm, half = period[:7], period[-1]
    day = "10" if half == "a" else "20"
    rec = {
        "id": f"00000000-0000-0000-0000-{i:012d}",
        "cluster_id": cluster or f"11111111-0000-0000-0000-{i:012d}",
        "published_at": f"{yyyy_mm}-{day}T01:02:03.123456+00:00",
        "display_date": f"{yyyy_mm}-09T09:00:00+00:00",
        "is_visible": i % 5 != 0,
        "title": f"로봇 동향 {i}",
        "analysis": {"id": f"a-{i}", "one_line_summary": "요약 " * 3, "verified_facts": ["사실"], "raw_response": None},
        # 재분석 이력 — 홀수 카드는 옛 분석 1건이 있다 (C2)
        "analysis_history": [{"id": f"h-{i}", "one_line_summary": "옛 요약", "validation_status": "WARN"}] if i % 2 else [],
        "related_sources": [
            {"url": "https://x/1", "title": "대표", "is_representative": True,
             "published_at": f"{yyyy_mm}-09T09:00:00+00:00", "source_name": "산업부"},
            {"source_name": "수동 등록", "url": "https://x/2", "title": None,
             "published_at": None, "is_representative": False},
        ],
        "body": "본문 " * 50 if i % 2 else None,
        "policy": {"published_item_id": f"00000000-0000-0000-0000-{i:012d}", "budget_amount_krw": 1.5e9} if i % 3 == 0 else None,
    }
    rec.update(over)
    return rec


# ------------------------------------------------------------
# 반달 기간 키 (Asia/Seoul) — C1
# ------------------------------------------------------------
class TestPeriod:
    def test_period_key_splits_month_at_kst_day_16(self):
        # UTC 7/15 14:59 = KST 7/15 23:59 → a, UTC 7/15 15:00 = KST 7/16 00:00 → b
        assert period_key(datetime(2026, 7, 15, 14, 59, tzinfo=UTC)) == "2026-07-a"
        assert period_key(datetime(2026, 7, 15, 15, 0, tzinfo=UTC)) == "2026-07-b"
        # UTC 8/31 15:30 = KST 9/1 00:30 → 9월 a
        assert period_key(datetime(2026, 8, 31, 15, 30, tzinfo=UTC)) == "2026-09-a"
        assert period_key(datetime(2026, 8, 31, 14, 59, tzinfo=UTC)) == "2026-08-b"
        assert period_key(datetime(2026, 8, 31, 14, 59)) == "2026-08-b"  # naive = UTC
        assert period_of_date(date(2026, 2, 15)) == "2026-02-a" and period_of_date(date(2026, 2, 16)) == "2026-02-b"

    def test_period_bounds_are_kst_instants(self):
        start, end = period_bounds("2026-08-a")
        assert start.astimezone(UTC) == datetime(2026, 7, 31, 15, 0, tzinfo=UTC)
        assert end.astimezone(UTC) == datetime(2026, 8, 15, 15, 0, tzinfo=UTC)
        start, end = period_bounds("2026-08-b")
        assert start.astimezone(UTC) == datetime(2026, 8, 15, 15, 0, tzinfo=UTC)
        assert end.astimezone(UTC) == datetime(2026, 8, 31, 15, 0, tzinfo=UTC)
        _, end = period_bounds("2026-12-b")
        assert (end.year, end.month, end.day) == (2027, 1, 1)
        # a와 b가 빈틈 없이 달을 덮는다
        assert period_bounds("2026-02-a")[1] == period_bounds("2026-02-b")[0]
        assert period_last_day("2026-02-a") == date(2026, 2, 15)
        assert period_last_day("2026-02-b") == date(2026, 2, 28)

    @pytest.mark.parametrize("bad", ["2026-08", "2026-8-a", "2026-08-c", "2026-13-a", "abc", "2026-08-01", "2026-08-a "])
    def test_period_bounds_rejects_bad_keys(self, bad):
        with pytest.raises(VaultError):
            period_bounds(bad)

    def test_due_needs_last_day_plus_window(self):
        # 8월 a(마지막 날 8/15) + 14일 = 8/29까지는 아님, 8/30부터
        assert period_is_due("2026-08-a", date(2026, 8, 29), 14) is False
        assert period_is_due("2026-08-a", date(2026, 8, 30), 14) is True
        # 8월 b(마지막 날 8/31) + 14일 → 9/15부터
        assert period_is_due("2026-08-b", date(2026, 9, 14), 14) is False
        assert period_is_due("2026-08-b", date(2026, 9, 15), 14) is True
        assert period_is_due("2026-07-b", date(2026, 9, 16), 30) is True

    def test_current_and_future_period_never_due(self):
        # 오늘 9/16 → 오늘 기간은 2026-09-b
        assert period_is_due("2026-09-b", date(2026, 9, 16), 0) is False
        assert period_is_due("2026-10-a", date(2026, 9, 16), 0) is False
        assert period_is_due("2026-09-a", date(2026, 9, 16), 0) is True  # 창 0이어도 지난 기간만
        # 오늘 9/15 → 오늘 기간은 2026-09-a → 아직 아님
        assert period_is_due("2026-09-a", date(2026, 9, 15), 0) is False

    def test_periods_due_skips_verified_and_stalled(self):
        manifest = {
            "2026-06-a": {"storage_verified_at": datetime(2026, 8, 1, tzinfo=UTC)},
            "2026-06-b": {"storage_verified_at": None, "verify_failures": 1},  # 내보냈지만 검증 안 됨 → 다시
            "2026-07-a": {"storage_verified_at": None, "verify_failures": VERIFY_STALL_LIMIT},  # C9 — 쉰다
        }
        candidates = ["2026-09-a", "2026-07-b", "2026-07-a", "2026-06-b", "2026-06-a", "2026-05-b", "2026-08-b"]
        due = periods_due(candidates, manifest, date(2026, 9, 16), 14)
        assert due == ["2026-05-b", "2026-06-b", "2026-07-b", "2026-08-b"]
        # 운영자가 0으로 되돌리면 재개
        manifest["2026-07-a"]["verify_failures"] = 0
        assert "2026-07-a" in periods_due(candidates, manifest, date(2026, 9, 16), 14)

    def test_window_floor_is_twice_merge_window(self, capsys):
        assert MIN_WINDOW_DAYS == 2 * MERGE_WINDOW_DAYS == 14
        assert effective_window_days(30) == 30
        assert effective_window_days(14) == 14
        assert effective_window_days(7) == 14
        assert effective_window_days("3") == 14
        assert effective_window_days(None) == 14
        assert "하한" in capsys.readouterr().err


# ------------------------------------------------------------
# 파일 형식 왕복 — HTTP·DB 없이
# ------------------------------------------------------------
class TestFileFormat:
    def test_record_key_order_is_fixed_and_includes_history(self):
        assert RECORD_KEYS.index("analysis_history") == RECORD_KEYS.index("analysis") + 1
        raw = {k: None for k in reversed(RECORD_KEYS)}
        raw.update({"id": "x", "extra": 1, "related_sources": [{"url": "u", "is_representative": True}]})
        rec = normalize_record(raw)
        assert list(rec) == list(RECORD_KEYS)
        assert "extra" not in rec
        assert rec["analysis_history"] == []  # null이 아니라 항상 배열
        assert list(rec["related_sources"][0]) == list(RELATED_SOURCE_KEYS)
        assert rec["related_sources"][0]["source_name"] is None  # 없는 키는 null

    def test_record_analysis_ids_include_current_and_history(self):
        assert record_analysis_ids(make_record(1)) == ["a-1", "h-1"]
        assert record_analysis_ids(make_record(2)) == ["a-2"]
        assert record_analysis_ids(make_record(3, analysis=None)) == ["h-3"]
        assert record_analysis_ids({"id": "x"}) == []

    def test_encode_is_single_gzip_member_utf8_line(self):
        member = encode_record(make_record(1))
        text = gzip.decompress(member).decode("utf-8")
        assert text.endswith("\n") and text.count("\n") == 1
        assert "로봇 동향 1" in text  # ensure_ascii=False
        parsed = json.loads(text)
        assert parsed["id"] == make_record(1)["id"]
        assert parsed["analysis_history"][0]["id"] == "h-1"

    def test_roundtrip_members_idx_and_whole_file(self, tmp_path):
        records = [make_record(i) for i in range(1, 6)]
        out = tmp_path / "m.jsonl.gz"
        with out.open("wb") as fh:
            pf = write_period_file(records, fh)
        data = out.read_bytes()

        assert pf.rows == 5 and pf.bytes == len(data)
        assert pf.sha256 == hashlib.sha256(data).hexdigest()
        # manifest.analysis_ids — current 포함 전부
        assert pf.analysis_ids == {r["id"]: record_analysis_ids(r) for r in records}
        assert pf.analysis_ids[records[0]["id"]] == ["a-1", "h-1"]
        # idx 구간은 파일을 빈틈 없이 덮는다
        spans = sorted(pf.idx.values())
        assert spans[0][0] == 0 and sum(s[1] for s in spans) == len(data)
        # 각 구간을 단독으로 gunzip → 같은 레코드
        for rec in records:
            offset, length = pf.idx[rec["id"]]
            got = read_member(data, offset, length)
            assert got == normalize_record(rec)
            assert list(got) == list(RECORD_KEYS)
        # 전체를 gunzip하면 JSONL (순서 유지)
        assert [r["id"] for r in iter_records(data)] == [r["id"] for r in records]
        # 표본 대조가 보는 필드가 그대로다
        assert compare_card(read_member(data, *pf.idx[records[2]["id"]]),
                            {"id": records[2]["id"], "title": records[2]["title"],
                             "published_at": datetime(2026, 7, 10, 1, 2, 3, 123456, tzinfo=UTC)}) == []

    def test_iter_records_splits_on_bytes_not_unicode_linebreaks(self):
        # 본문에 U+2028(LINE SEPARATOR)·U+0085 — str.splitlines()는 여기서 줄을 쪼갠다
        rec = make_record(1, body="첫 줄 둘째 줄셋째", title="제목 끝")
        buf = io.BytesIO()
        write_period_file([rec, make_record(2)], buf)
        got = list(iter_records(buf.getvalue()))
        assert len(got) == 2
        assert got[0]["body"] == "첫 줄 둘째 줄셋째" and got[0]["title"] == "제목 끝"

    def test_duplicate_id_refused(self):
        with pytest.raises(VaultError):
            write_period_file([make_record(1), make_record(1)], io.BytesIO())

    def test_empty_period_file(self):
        pf = write_period_file([], io.BytesIO())
        assert pf.rows == 0 and pf.bytes == 0 and pf.idx == {} and pf.analysis_ids == {}

    def test_json_default_handles_db_types(self):
        from decimal import Decimal
        from uuid import UUID
        rec = make_record(1, analysis={
            "generated_at": datetime(2026, 7, 1, tzinfo=UTC), "d": date(2026, 7, 1),
            "u": UUID(int=5), "n": Decimal("1.5"),
        })
        got = decode_member(encode_record(rec))["analysis"]
        assert got == {"generated_at": "2026-07-01T00:00:00+00:00", "d": "2026-07-01",
                       "u": "00000000-0000-0000-0000-000000000005", "n": 1.5}


def _build(records):
    buf = io.BytesIO()
    pf = write_period_file(records, buf)
    data = buf.getvalue()
    manifest = {"rows": pf.rows, "bytes": pf.bytes, "sha256": pf.sha256, "sample_ids": list(pf.idx)[:2]}
    return data, pf.idx, manifest


class TestCheckFile:
    def test_passes_when_consistent(self):
        data, idx, manifest = _build([make_record(i) for i in range(3)])
        assert check_file(data, idx, manifest) == []

    def test_detects_tampered_bytes_and_sha(self):
        data, idx, manifest = _build([make_record(i) for i in range(3)])
        problems = check_file(data[:-1] + bytes([data[-1] ^ 0xFF]), idx, manifest)
        assert any("sha256" in p for p in problems)
        assert not any("바이트 수" in p for p in problems)

    def test_detects_truncation(self):
        data, idx, manifest = _build([make_record(i) for i in range(3)])
        assert any("바이트" in p for p in check_file(data[:-10], idx, manifest))

    def test_detects_row_and_idx_mismatch(self):
        data, idx, manifest = _build([make_record(i) for i in range(3)])
        manifest["rows"] = 4
        problems = check_file(data, idx, manifest)
        assert any("행 수" in p for p in problems) and any("idx 항목" in p for p in problems)

    def test_detects_idx_gap_and_missing_sample(self):
        data, idx, manifest = _build([make_record(i) for i in range(3)])
        broken = dict(idx)
        first = next(iter(broken))
        broken[first] = [broken[first][0], broken[first][1] - 1]
        assert any("구간" in p for p in check_file(data, broken, manifest))
        manifest["sample_ids"] = ["nope"]
        assert any("표본" in p for p in check_file(data, idx, manifest))


class TestCompareCard:
    def test_equal_instants_in_different_forms(self):
        rec = {"id": "a", "title": "t", "published_at": "2026-07-10T10:02:03.123456+09:00"}
        card = {"id": "a", "title": "t", "published_at": datetime(2026, 7, 10, 1, 2, 3, 123456, tzinfo=UTC)}
        assert compare_card(rec, card) == []

    def test_reports_each_difference(self):
        rec = {"id": "a", "title": "t", "published_at": "2026-07-10T01:02:03+00:00"}
        card = {"id": "b", "title": "u", "published_at": datetime(2026, 7, 10, 1, 2, 4, tzinfo=UTC)}
        assert len(compare_card(rec, card)) == 3
        assert compare_card({"id": "a", "title": "t", "published_at": None}, card) != []


class TestSplitPruneBatch:
    RECORDED = {"p1": ["a1", "h1"], "p2": ["a2"], "p3": []}

    def test_keeps_cards_whose_current_is_recorded(self):
        batch = [{"id": "p1", "cluster_id": "c1", "current_analysis_id": "h1"},  # 옛 분석이 current여도 기록돼 있으면 OK
                 {"id": "p2", "cluster_id": "c2", "current_analysis_id": "a2"}]
        ok, skipped = split_prune_batch(batch, self.RECORDED)
        assert [c["id"] for c in ok] == ["p1", "p2"] and skipped == {}

    def test_skips_reanalyzed_missing_and_unknown_cards(self):
        batch = [{"id": "p1", "cluster_id": "c1", "current_analysis_id": "a-new"},  # 내보낸 뒤 재분석
                 {"id": "p2", "cluster_id": "c2", "current_analysis_id": None},     # current 없음
                 {"id": "p3", "cluster_id": "c3", "current_analysis_id": "a3"},     # 기록이 비어 있음
                 {"id": "p9", "cluster_id": "c9", "current_analysis_id": "a9"}]     # manifest에 카드 없음
        ok, skipped = split_prune_batch(batch, self.RECORDED)
        assert ok == [] and set(skipped) == {"p1", "p2", "p3", "p9"}
        assert "재분석" in skipped["p1"]["reason"] and skipped["p1"]["recorded"] == ["a1", "h1"]
        assert skipped["p2"]["current_analysis_id"] is None


class TestFindMismatches:
    def test_clean_state(self):
        manifest = {"2026-07-a": {"rows": 10, "storage_verified_at": 1, "pruned_at": 1, "pruned_rows": 10}}
        stats = {"2026-07-a": {"total": 10, "vaulted": 10}, "2026-08-a": {"total": 5, "vaulted": 0}}
        assert find_mismatches(manifest, stats) == []

    def test_vaulted_cards_without_verified_manifest_is_critical(self):
        stats = {"2026-07-a": {"total": 10, "vaulted": 3}}
        assert len(find_mismatches({}, stats)) >= 1
        assert len(find_mismatches({"2026-07-a": {"rows": 10, "storage_verified_at": None}}, stats)) >= 1

    def test_row_count_drift_and_pruned_rows(self):
        manifest = {"2026-07-a": {"rows": 10, "storage_verified_at": 1, "pruned_at": 1, "pruned_rows": 10}}
        stats = {"2026-07-a": {"total": 11, "vaulted": 9}}
        problems = find_mismatches(manifest, stats)
        assert any("≠ manifest rows" in p for p in problems)
        assert any("pruned_rows" in p for p in problems)
        assert any("합계" in p for p in problems)


class TestFindMirrorStale:
    NOW = datetime(2026, 9, 16, tzinfo=UTC)

    def test_flags_only_old_unmirrored_periods(self):
        assert MIRROR_STALE_DAYS == 14
        manifest = {
            "2026-05-a": {"storage_verified_at": self.NOW - timedelta(days=15), "release_verified_at": None},
            "2026-05-b": {"storage_verified_at": (self.NOW - timedelta(days=20)).isoformat(), "release_verified_at": self.NOW},
            "2026-06-a": {"storage_verified_at": self.NOW - timedelta(days=13), "release_verified_at": None},
            "2026-06-b": {"storage_verified_at": None, "release_verified_at": None},
        }
        stale = find_mirror_stale(manifest, self.NOW)
        assert len(stale) == 1 and stale[0].startswith("2026-05-a") and "15일" in stale[0]

    def test_unparsable_marks_are_ignored(self):
        assert find_mirror_stale({"2026-05-a": {"storage_verified_at": "now", "release_verified_at": None}}, self.NOW) == []


# ------------------------------------------------------------
# Storage 클라이언트 — httpx.MockTransport
# ------------------------------------------------------------
class FakeStorage:
    """Supabase Storage REST 흉내: 버킷 1개, 객체 dict, Range 206, HEAD. 헤더 규칙(C6) 검사."""

    def __init__(self, bucket_exists: bool = False, key: str = KEY):
        self.objects: dict[str, bytes] = {}
        self.bucket_exists = bucket_exists
        self.key = key
        self.requests: list[httpx.Request] = []
        self.serve_override: dict[str, bytes] = {}  # 내려받기만 바꿔치기 (변조 시험)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def _auth_ok(self, request: httpx.Request) -> bool:
        if request.headers.get("apikey") != self.key:
            return False
        if self.key.startswith(("sb_secret_", "sb_publishable_")):
            return "authorization" not in request.headers  # 새 키는 Bearer를 보내면 거부
        return request.headers.get("authorization") == f"Bearer {self.key}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._auth_ok(request):
            return httpx.Response(401, json={"message": "no auth"})
        path = request.url.path
        if path == "/storage/v1/bucket/vault" and request.method == "GET":
            return httpx.Response(200, json={"id": "vault"}) if self.bucket_exists else \
                httpx.Response(404, json={"statusCode": "404", "error": "Bucket not found"})
        if path == "/storage/v1/bucket" and request.method == "POST":
            body = json.loads(request.content)
            assert body == {"id": "vault", "name": "vault", "public": False}
            if self.bucket_exists:
                return httpx.Response(409, json={"error": "Duplicate", "message": "The resource already exists"})
            self.bucket_exists = True
            return httpx.Response(200, json={"name": "vault"})
        upload_prefix = "/storage/v1/object/vault/"
        if path.startswith(upload_prefix) and request.method == "POST":
            assert request.headers.get("x-upsert") == "true"
            self.objects[path[len(upload_prefix):]] = request.content
            return httpx.Response(200, json={"Key": "vault/" + path[len(upload_prefix):]})
        read_prefix = "/storage/v1/object/authenticated/vault/"
        if path.startswith(read_prefix) and request.method in ("GET", "HEAD"):
            key = path[len(read_prefix):]
            if key not in self.objects:
                return httpx.Response(404, json={"statusCode": "404", "error": "not_found"})
            data = self.serve_override.get(key, self.objects[key])
            if request.method == "HEAD":
                return httpx.Response(200, headers={"content-length": str(len(data))})
            rng = request.headers.get("range")
            if rng:
                m = re.fullmatch(r"bytes=(\d+)-(\d+)", rng)
                a, b = int(m.group(1)), int(m.group(2))
                return httpx.Response(
                    206, content=data[a:b + 1],
                    headers={"content-range": f"bytes {a}-{b}/{len(data)}"},
                )
            return httpx.Response(200, content=data)
        return httpx.Response(500, text=f"unexpected {request.method} {path}")


class TestVaultStorage:
    def test_requires_credentials(self):
        with pytest.raises(VaultError):
            VaultStorage("", KEY)
        with pytest.raises(VaultError):
            VaultStorage(SUPABASE_URL, "")

    def test_header_rule_legacy_jwt_key(self):
        # C6 — apikey는 항상, Authorization: Bearer는 옛(JWT) 키일 때만
        assert auth_headers(KEY) == {"apikey": KEY, "Authorization": f"Bearer {KEY}"}

    @pytest.mark.parametrize("key", [NEW_KEY, "sb_publishable_xyz"])
    def test_header_rule_new_keys_have_no_bearer(self, key):
        assert auth_headers(key) == {"apikey": key}
        fake = FakeStorage(bucket_exists=True, key=key)
        st = VaultStorage(SUPABASE_URL, key, client=fake.client())
        st.upload("items/2026-07-a.jsonl.gz", b"abc", "application/gzip")
        assert st.download("items/2026-07-a.jsonl.gz") == b"abc"
        for r in fake.requests:
            assert r.headers["apikey"] == key and "authorization" not in r.headers

    def test_ensure_bucket_creates_once(self):
        fake = FakeStorage(bucket_exists=False)
        st = VaultStorage(SUPABASE_URL + "/", KEY, client=fake.client())
        st.ensure_bucket()
        assert fake.bucket_exists
        assert [r.method for r in fake.requests] == ["GET", "POST"]
        st.ensure_bucket()  # 이미 있으면 GET 한 번으로 끝
        assert [r.method for r in fake.requests][2:] == ["GET"]

    def test_ensure_bucket_tolerates_race(self):
        fake = FakeStorage(bucket_exists=False)
        st = VaultStorage(SUPABASE_URL, KEY, client=fake.client())
        original = fake.handler

        def racy(request):
            if request.method == "POST" and request.url.path == "/storage/v1/bucket":
                fake.bucket_exists = True
            return original(request)

        st.client = httpx.Client(transport=httpx.MockTransport(racy))
        st.ensure_bucket()

    def test_upload_download_range_head(self):
        fake = FakeStorage(bucket_exists=True)
        st = VaultStorage(SUPABASE_URL, KEY, client=fake.client())
        st.upload("items/2026-07-a.jsonl.gz", b"0123456789", "application/gzip")
        up = fake.requests[-1]
        assert up.headers["content-type"] == "application/gzip"
        assert up.headers["apikey"] == KEY and up.headers["authorization"] == f"Bearer {KEY}"
        assert up.headers["x-upsert"] == "true"
        assert st.download("items/2026-07-a.jsonl.gz") == b"0123456789"
        assert fake.requests[-1].url.path == "/storage/v1/object/authenticated/vault/items/2026-07-a.jsonl.gz"
        assert st.download_range("items/2026-07-a.jsonl.gz", 3, 4) == b"3456"
        assert fake.requests[-1].headers["range"] == "bytes=3-6"
        assert st.exists("items/2026-07-a.jsonl.gz") is True
        assert st.exists("items/2026-06-b.jsonl.gz") is False
        with pytest.raises(VaultError):
            st.download("items/2026-06-b.jsonl.gz")

    def test_range_must_be_206_with_exact_length(self):
        no_range = lambda req: httpx.Response(200, content=b"abcdef")  # noqa: E731
        st = VaultStorage(SUPABASE_URL, KEY, client=httpx.Client(transport=httpx.MockTransport(no_range)))
        with pytest.raises(VaultError, match="206"):
            st.download_range("items/m.jsonl.gz", 0, 2)
        short = lambda req: httpx.Response(206, content=b"a")  # noqa: E731
        st = VaultStorage(SUPABASE_URL, KEY, client=httpx.Client(transport=httpx.MockTransport(short)))
        with pytest.raises(VaultError, match="길이"):
            st.download_range("items/m.jsonl.gz", 0, 2)

    def test_paths_use_period_key(self):
        assert items_path("2026-07-a") == "items/2026-07-a.jsonl.gz"
        assert index_path("2026-07-b") == "items/2026-07-b.idx.json"


# ------------------------------------------------------------
# 가짜 DB — VaultDB와 같은 메서드
# ------------------------------------------------------------
class FakeDB:
    def __init__(self, records_by_period: dict[str, list[dict]], manifest: dict | None = None,
                 ready: bool = True, active_clusters: set[str] | None = None):
        self.records = records_by_period
        self.manifest = {m: {"verify_failures": 0, "analysis_ids": {}, **r} for m, r in (manifest or {}).items()}
        self.ready = ready
        self.active_clusters = active_clusters or set()
        self.vaulted: dict[str, set[str]] = {m: set() for m in records_by_period}
        # 카드의 현재 current_analysis_id — 기본은 레코드의 analysis.id. 재분석은 테스트가 바꾼다
        self.current: dict[str, str | None] = {
            r["id"]: (r["analysis"] or {}).get("id") for recs in records_by_period.values() for r in recs
        }
        self.events: list[tuple[str, str, dict]] = []
        self.batches: list[tuple[list[str], list[str]]] = []
        self.candidate_calls: list[list[str]] = []  # prune_candidates가 받은 exclude_ids
        self.deleted_analyses: list[str] = []
        self.rollbacks = 0
        self.end_reads = 0
        self.conn = SimpleNamespace(rollback=self._rollback)

    def _rollback(self):
        self.rollbacks += 1

    def _period(self, start):
        return period_key(start)

    def require_ready(self):
        if not self.ready:
            raise VaultError("vault_manifest 표가 없습니다")

    def end_read(self):
        self.end_reads += 1

    def period_candidates(self):
        return sorted(self.records)

    def manifest_rows(self):
        return {m: dict(r) for m, r in self.manifest.items()}

    def iter_period_records(self, start, end):
        yield from self.records.get(self._period(start), [])

    def period_counts(self, start, end):
        m = self._period(start)
        return len(self.records.get(m, [])), len(self.vaulted.get(m, set()))

    def load_cards(self, ids):
        wanted = set(ids)
        return {
            r["id"]: {"id": r["id"], "title": r["title"], "published_at": r["published_at"]}
            for recs in self.records.values() for r in recs if r["id"] in wanted
        }

    def upsert_manifest(self, row):
        previous = self.manifest.get(row["month"], {})
        self.manifest[row["month"]] = {
            **row, "exported_at": "now", "storage_verified_at": None,
            "release_verified_at": None, "release_tag": None, "pruned_at": None, "pruned_rows": None,
            "verify_failures": previous.get("verify_failures", 0),  # 재내보내기가 실패 횟수를 지우지 않는다
        }

    def mark_storage_verified(self, period):
        self.manifest[period]["storage_verified_at"] = "now"
        self.manifest[period]["verify_failures"] = 0

    def bump_verify_failures(self, period):
        row = self.manifest.get(period)
        if row is None:
            return 0
        row["verify_failures"] = row.get("verify_failures", 0) + 1
        return row["verify_failures"]

    def record_event(self, event_type, reason, detail):
        self.events.append((event_type, reason, detail))

    def prunable_periods(self):
        return sorted(
            m for m, r in self.manifest.items()
            if r.get("storage_verified_at") and r.get("release_verified_at") and not r.get("pruned_at")
        )

    def prune_candidates(self, start, end, limit, exclude_ids=None):
        m = self._period(start)
        exclude = set(exclude_ids or [])
        self.candidate_calls.append(sorted(exclude))
        rows = [
            {"id": r["id"], "cluster_id": r["cluster_id"], "current_analysis_id": self.current[r["id"]]}
            for r in self.records.get(m, [])
            if r["id"] not in self.vaulted[m] and r["cluster_id"] not in self.active_clusters
            and r["id"] not in exclude
        ]
        return rows[:limit]

    def prune_batch(self, ids, analysis_ids):
        self.batches.append((list(ids), list(analysis_ids)))
        self.deleted_analyses += list(analysis_ids)
        for m in self.vaulted:
            self.vaulted[m].update(i for i in ids if any(r["id"] == i for r in self.records[m]))
        return len(ids)

    def finish_prune(self, period, start, end):
        n = len(self.vaulted[period])
        self.manifest[period].update(pruned_at="now", pruned_rows=n)
        return n

    def period_stats(self):
        return {m: {"total": len(recs), "vaulted": len(self.vaulted[m])} for m, recs in self.records.items()}


def _storage_pair(bucket_exists=False):
    fake = FakeStorage(bucket_exists=bucket_exists)
    st = VaultStorage(SUPABASE_URL, KEY, client=fake.client())
    return fake, st


# ------------------------------------------------------------
# a)+b) export → verify
# ------------------------------------------------------------
class TestExportVerify:
    TODAY = date(2026, 9, 16)  # 오늘 기간 2026-09-b. 창 14 → 2026-08-b(마지막 8/31)는 9/15부터 대상

    def test_exports_due_period_and_verifies(self):
        july = [make_record(i, "2026-07-a") for i in range(1, 31)]
        sep = [make_record(i, "2026-09-a") for i in range(100, 105)]
        db = FakeDB({"2026-07-a": july, "2026-09-a": sep})
        fake, st = _storage_pair()
        n = run_export_verify(db, lambda: st, 14, today=self.TODAY)
        assert n == 1
        row = db.manifest["2026-07-a"]
        assert row["storage_verified_at"] == "now" and row["rows"] == 30 and row["verify_failures"] == 0
        assert row["storage_path"] == "items/2026-07-a.jsonl.gz"
        assert 1 <= len(row["sample_ids"]) <= 20 and set(row["sample_ids"]) <= {r["id"] for r in july}
        # C2 — manifest.analysis_ids에 current 포함 전부
        assert row["analysis_ids"] == {r["id"]: record_analysis_ids(r) for r in july}
        assert "2026-09-a" not in db.manifest  # 9/15 + 14일 = 9/29 이후에야
        assert db.events == []
        # 올라간 파일이 형식대로다
        data = fake.objects["items/2026-07-a.jsonl.gz"]
        idx = json.loads(fake.objects["items/2026-07-a.idx.json"])
        assert len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"]
        assert list(idx) == [r["id"] for r in july]  # 파일 순서 = idx 순서
        got = read_member(data, *idx[july[7]["id"]])
        assert got == normalize_record(july[7]) and "analysis_history" in got
        # 검증은 Range 요청으로 표본을 읽었다
        ranges = [r for r in fake.requests if r.headers.get("range")]
        assert len(ranges) == len(row["sample_ids"])

    def test_half_month_files_are_separate(self):
        db = FakeDB({"2026-07-a": [make_record(1, "2026-07-a")], "2026-07-b": [make_record(2, "2026-07-b")]})
        fake, st = _storage_pair()
        assert run_export_verify(db, lambda: st, 14, today=self.TODAY) == 2
        assert set(fake.objects) == {"items/2026-07-a.jsonl.gz", "items/2026-07-a.idx.json",
                                     "items/2026-07-b.jsonl.gz", "items/2026-07-b.idx.json"}
        assert [r["id"] for r in iter_records(fake.objects["items/2026-07-b.jsonl.gz"])] == [make_record(2)["id"]]

    def test_sample_size_is_twenty_for_big_periods(self):
        july = [make_record(i, "2026-07-a") for i in range(1, 60)]
        db = FakeDB({"2026-07-a": july})
        _, st = _storage_pair()
        run_export_verify(db, lambda: st, 14, today=self.TODAY)
        assert len(db.manifest["2026-07-a"]["sample_ids"]) == 20

    def test_no_due_period_does_not_touch_storage(self):
        db = FakeDB({"2026-09-b": [make_record(1, "2026-09-b")]})

        def boom():
            raise AssertionError("storage must not be created")

        assert run_export_verify(db, boom, 14, today=self.TODAY) == 0

    def test_requires_migration(self):
        db = FakeDB({"2026-07-a": [make_record(1)]}, ready=False)
        with pytest.raises(VaultError, match="vault_manifest"):
            run_export_verify(db, lambda: None, 14, today=self.TODAY)

    def test_tampered_download_fails_verification_and_records(self):
        july = [make_record(i, "2026-07-a") for i in range(1, 6)]
        db = FakeDB({"2026-07-a": july})
        fake, st = _storage_pair()
        original_upload = st.upload

        def upload_then_corrupt(path, data, content_type):
            original_upload(path, data, content_type)
            if path.endswith(".jsonl.gz"):
                fake.serve_override[path] = data[:-1] + bytes([data[-1] ^ 0xFF])

        st.upload = upload_then_corrupt
        with pytest.raises(VaultError, match="검증 실패 2026-07-a"):
            run_export_verify(db, lambda: st, 14, today=self.TODAY)
        assert db.manifest["2026-07-a"]["storage_verified_at"] is None
        assert db.manifest["2026-07-a"]["verify_failures"] == 1  # C9
        assert [e[0] for e in db.events] == ["VAULT_VERIFY_FAILED"]
        assert db.events[0][2]["month"] == "2026-07-a"

    def test_verify_failures_stall_after_limit_and_resume_on_reset(self):
        july = [make_record(i, "2026-07-a") for i in range(1, 6)]
        db = FakeDB({"2026-07-a": july})
        fake, st = _storage_pair()
        original_upload = st.upload

        def upload_then_corrupt(path, data, content_type):
            original_upload(path, data, content_type)
            if path.endswith(".jsonl.gz"):
                fake.serve_override[path] = data[:-1] + bytes([data[-1] ^ 0xFF])

        st.upload = upload_then_corrupt
        assert VERIFY_STALL_LIMIT == 3
        for _ in range(VERIFY_STALL_LIMIT):
            with pytest.raises(VaultError):
                run_export_verify(db, lambda: st, 14, today=self.TODAY)
        assert db.manifest["2026-07-a"]["verify_failures"] == 3
        types = [e[0] for e in db.events]
        assert types.count("VAULT_VERIFY_FAILED") == 3 and types.count("VAULT_VERIFY_STALLED") == 1
        assert types[-1] == "VAULT_VERIFY_STALLED"
        # 4번째 실행 — 그 기간은 건너뛴다 (예외 없음, 기록 추가 없음, Storage 안 건드림)
        uploads_before = len(fake.requests)
        assert run_export_verify(db, lambda: st, 14, today=self.TODAY) == 0
        assert len(fake.requests) == uploads_before and len(db.events) == 4
        # 운영자가 0으로 되돌리고 파일이 정상이면 재개 → 통과 → verify_failures 0
        db.manifest["2026-07-a"]["verify_failures"] = 0
        fake.serve_override.clear()
        st.upload = original_upload
        assert run_export_verify(db, lambda: st, 14, today=self.TODAY) == 1
        assert db.manifest["2026-07-a"]["storage_verified_at"] == "now"
        assert db.manifest["2026-07-a"]["verify_failures"] == 0

    def test_sample_mismatch_with_db_fails(self):
        july = [make_record(i, "2026-07-a") for i in range(1, 6)]
        db = FakeDB({"2026-07-a": july})
        _, st = _storage_pair()
        original = db.load_cards

        def changed(ids):
            cards = original(ids)
            for c in cards.values():
                c["title"] = "바뀐 제목"
            return cards

        db.load_cards = changed
        with pytest.raises(VaultError):
            run_export_verify(db, lambda: st, 14, today=self.TODAY)
        assert any("title" in p for p in db.events[0][2]["problems"])

    def test_refuses_to_reexport_period_with_vaulted_cards(self):
        july = [make_record(i, "2026-07-a") for i in range(1, 4)]
        db = FakeDB({"2026-07-a": july}, manifest={"2026-07-a": {"rows": 3, "storage_verified_at": None}})
        db.vaulted["2026-07-a"].add(july[0]["id"])
        fake, st = _storage_pair()
        with pytest.raises(VaultError):
            run_export_verify(db, lambda: st, 14, today=self.TODAY)
        assert fake.objects == {}  # 덮어쓰지 않았다
        assert db.events[0][0] == "VAULT_VERIFY_FAILED" and "이미 금고" in db.events[0][1]
        assert db.manifest["2026-07-a"]["verify_failures"] == 1

    def test_period_isolation_and_cap(self):
        periods = {f"2026-0{m}-{h}": [make_record(m * 10 + i + (5 if h == "b" else 0), f"2026-0{m}-{h}") for i in range(2)]
                   for m in (5, 6, 7) for h in "ab"}
        db = FakeDB(periods)
        _, st = _storage_pair()
        assert MAX_PERIODS_PER_RUN == 3
        assert run_export_verify(db, lambda: st, 14, today=self.TODAY) == 3
        assert sorted(db.manifest) == ["2026-05-a", "2026-05-b", "2026-06-a"]  # 오래된 순
        assert run_export_verify(db, lambda: st, 14, today=self.TODAY) == 3
        assert sorted(db.manifest) == sorted(periods)

    def test_one_bad_period_does_not_block_others(self):
        db = FakeDB({"2026-06-a": [make_record(1, "2026-06-a")], "2026-07-a": [make_record(2, "2026-07-a")]})
        _, st = _storage_pair()
        original = db.iter_period_records

        def flaky(start, end):
            if period_key(start) == "2026-06-a":
                raise RuntimeError("DB 끊김")
            return original(start, end)

        db.iter_period_records = flaky
        with pytest.raises(VaultError, match="2026-06-a"):
            run_export_verify(db, lambda: st, 14, today=self.TODAY)
        assert db.manifest["2026-07-a"]["storage_verified_at"] == "now"
        assert "2026-06-a" not in db.manifest
        assert db.rollbacks == 1


# ------------------------------------------------------------
# c) prune
# ------------------------------------------------------------
def _verified_manifest(period, records, release=True, analysis_ids=None):
    if analysis_ids is None:
        analysis_ids = {r["id"]: record_analysis_ids(r) for r in records}
    return {period: {"rows": len(records), "storage_verified_at": "s", "release_verified_at": "r" if release else None,
                     "pruned_at": None, "pruned_rows": None, "analysis_ids": analysis_ids}}


class TestPrune:
    def test_disabled_does_nothing(self):
        july = [make_record(1)]
        db = FakeDB({"2026-07-a": july}, manifest=_verified_manifest("2026-07-a", july))
        assert run_prune(db, enabled=False, max_per_run=3000) == 0
        assert db.batches == [] and db.vaulted["2026-07-a"] == set()

    def test_needs_both_verifications(self):
        july = [make_record(1)]
        db = FakeDB({"2026-07-a": july}, manifest=_verified_manifest("2026-07-a", july, release=False))
        assert run_prune(db, enabled=True, max_per_run=3000) == 0
        assert db.batches == []

    def test_batches_of_200_and_completion(self):
        july = [make_record(i, "2026-07-a") for i in range(450)]
        db = FakeDB({"2026-07-a": july}, manifest=_verified_manifest("2026-07-a", july))
        assert PRUNE_BATCH_ROWS == 200
        assert run_prune(db, enabled=True, max_per_run=3000) == 450
        assert [len(ids) for ids, _ in db.batches] == [200, 200, 50]
        assert db.manifest["2026-07-a"]["pruned_at"] == "now"
        assert db.manifest["2026-07-a"]["pruned_rows"] == 450

    def test_deletes_only_recorded_analysis_ids(self):
        # C3 — 배치가 지우는 분석 id = 그 배치 카드들의 manifest.analysis_ids 합집합, 그 외는 절대 아님
        july = [make_record(i, "2026-07-a") for i in range(1, 5)]
        db = FakeDB({"2026-07-a": july}, manifest=_verified_manifest("2026-07-a", july))
        assert run_prune(db, enabled=True, max_per_run=3000) == 4
        ids, analysis_ids = db.batches[0]
        assert ids == [r["id"] for r in july]
        assert analysis_ids == sorted(["a-1", "h-1", "a-2", "a-3", "h-3", "a-4"])
        assert db.events == []

    def test_skips_reanalyzed_cards_and_records_mismatch(self):
        # C3 — 내보낸 뒤 재분석된 카드(current가 기록에 없음)는 통째로 건너뛴다
        july = [make_record(i, "2026-07-a") for i in range(5)]
        db = FakeDB({"2026-07-a": july}, manifest=_verified_manifest("2026-07-a", july))
        db.current[july[2]["id"]] = "a-2-reanalyzed"
        assert run_prune(db, enabled=True, max_per_run=3000) == 4
        ids, analysis_ids = db.batches[0]
        assert july[2]["id"] not in ids and "a-2" not in analysis_ids
        assert july[2]["id"] not in db.vaulted["2026-07-a"]
        assert db.manifest["2026-07-a"]["pruned_at"] is None  # 기간을 닫지 않는다
        assert [e[0] for e in db.events] == ["VAULT_MISMATCH"]
        event = db.events[0]
        assert "재분석" in event[1] and event[2]["stage"] == "prune"
        assert event[2]["skipped"][0]["id"] == july[2]["id"]
        assert event[2]["skipped"][0]["current_analysis_id"] == "a-2-reanalyzed"
        assert event[2]["skipped"][0]["recorded"] == ["a-2"]
        # 다음 실행도 그 카드는 건너뛰고(무한 반복 없음) 기록만 다시 남긴다
        assert run_prune(db, enabled=True, max_per_run=3000) == 0
        assert db.batches[1:] == [] and len(db.events) == 2

    def test_skipped_cards_are_excluded_from_following_candidate_queries(self):
        july = [make_record(i, "2026-07-a") for i in range(PRUNE_BATCH_ROWS + 10)]
        db = FakeDB({"2026-07-a": july}, manifest=_verified_manifest("2026-07-a", july))
        db.current[july[0]["id"]] = "new-0"
        assert run_prune(db, enabled=True, max_per_run=3000) == PRUNE_BATCH_ROWS + 9
        # 두 번째 후보 조회부터 exclude_ids에 건너뛴 카드가 들어간다
        assert db.candidate_calls[0] == [] and db.candidate_calls[1] == [july[0]["id"]]
        assert len(db.events) == 1

    def test_max_per_run_and_resume(self):
        july = [make_record(i, "2026-07-a") for i in range(500)]
        db = FakeDB({"2026-07-a": july}, manifest=_verified_manifest("2026-07-a", july))
        assert run_prune(db, enabled=True, max_per_run=300) == 300
        assert [len(ids) for ids, _ in db.batches] == [200, 100]
        assert db.manifest["2026-07-a"]["pruned_at"] is None  # 아직
        assert run_prune(db, enabled=True, max_per_run=300) == 200
        assert db.manifest["2026-07-a"]["pruned_at"] == "now"

    def test_skips_clusters_with_live_jobs_and_leaves_period_open(self):
        july = [make_record(i, "2026-07-a") for i in range(5)]
        db = FakeDB({"2026-07-a": july}, manifest=_verified_manifest("2026-07-a", july),
                    active_clusters={july[2]["cluster_id"]})
        assert run_prune(db, enabled=True, max_per_run=3000) == 4
        assert july[2]["id"] not in db.vaulted["2026-07-a"]
        assert db.manifest["2026-07-a"]["pruned_at"] is None
        db.active_clusters.clear()
        assert run_prune(db, enabled=True, max_per_run=3000) == 1
        assert db.manifest["2026-07-a"]["pruned_rows"] == 5

    def test_counts_only_cards_actually_marked(self):
        # prune_batch가 표식을 찍지 못한 카드(문장 안에서 살아 있는 job이 발견됨)는 세지 않는다
        july = [make_record(i, "2026-07-a") for i in range(3)]
        db = FakeDB({"2026-07-a": july}, manifest=_verified_manifest("2026-07-a", july))
        original = db.prune_batch

        def partial(ids, analysis_ids):
            original(ids[:-1], analysis_ids)
            return len(ids) - 1

        db.prune_batch = partial
        assert run_prune(db, enabled=True, max_per_run=3000) == 2
        assert db.manifest["2026-07-a"]["pruned_at"] is None

    def test_refuses_when_card_count_drifted(self):
        july = [make_record(i, "2026-07-a") for i in range(5)]
        db = FakeDB({"2026-07-a": july}, manifest=_verified_manifest("2026-07-a", july[:4]))
        assert run_prune(db, enabled=True, max_per_run=3000) == 0
        assert db.batches == []
        assert db.events[0][0] == "VAULT_MISMATCH"

    def test_oldest_period_first_and_cap_across_periods(self):
        june = [make_record(i, "2026-06-b") for i in range(3)]
        july = [make_record(10 + i, "2026-07-a") for i in range(3)]
        db = FakeDB({"2026-06-b": june, "2026-07-a": july},
                    manifest={**_verified_manifest("2026-06-b", june), **_verified_manifest("2026-07-a", july)})
        assert run_prune(db, enabled=True, max_per_run=4) == 4
        assert db.manifest["2026-06-b"]["pruned_at"] == "now"
        assert db.manifest["2026-07-a"]["pruned_at"] is None and len(db.vaulted["2026-07-a"]) == 1


# ------------------------------------------------------------
# d) selfcheck
# ------------------------------------------------------------
class TestSelfcheck:
    def test_clean(self):
        july = [make_record(i, "2026-07-a") for i in range(3)]
        db = FakeDB({"2026-07-a": july}, manifest=_verified_manifest("2026-07-a", july))
        fake, st = _storage_pair(bucket_exists=True)
        fake.objects["items/2026-07-a.jsonl.gz"] = b"x"
        fake.objects["items/2026-07-a.idx.json"] = b"{}"
        assert run_selfcheck(db, lambda: st) == 0
        assert db.events == []
        assert [r.method for r in fake.requests] == ["HEAD", "HEAD"]

    def test_missing_file_and_orphan_vaulted_cards(self):
        july = [make_record(i, "2026-07-a") for i in range(3)]
        db = FakeDB({"2026-07-a": july, "2026-06-b": [make_record(9, "2026-06-b")]},
                    manifest=_verified_manifest("2026-07-a", july))
        db.vaulted["2026-06-b"].add(make_record(9, "2026-06-b")["id"])  # 대장 없이 비워짐
        fake, st = _storage_pair(bucket_exists=True)
        fake.objects["items/2026-07-a.jsonl.gz"] = b"x"  # idx 없음
        n = run_selfcheck(db, lambda: st)
        assert n == 2
        assert db.events[0][0] == "VAULT_MISMATCH"
        problems = db.events[0][2]["problems"]
        assert any("2026-06-b" in p for p in problems) and any("idx.json" in p for p in problems)

    def test_mirror_stale_is_recorded_separately(self):
        # C10 — Storage 검증 뒤 14일 넘게 Release 미러가 없으면 VAULT_MIRROR_STALE
        now = datetime(2026, 9, 16, tzinfo=UTC)
        july = [make_record(i, "2026-07-a") for i in range(2)]
        db = FakeDB({"2026-07-a": july}, manifest=_verified_manifest("2026-07-a", july, release=False))
        db.manifest["2026-07-a"]["storage_verified_at"] = now - timedelta(days=15)
        fake, st = _storage_pair(bucket_exists=True)
        fake.objects["items/2026-07-a.jsonl.gz"] = b"x"
        fake.objects["items/2026-07-a.idx.json"] = b"{}"
        assert run_selfcheck(db, lambda: st, now=now) == 1
        assert [e[0] for e in db.events] == ["VAULT_MIRROR_STALE"]
        assert "2026-07-a" in db.events[0][2]["problems"][0] and db.events[0][2]["stale_days"] == 14
        # 13일이면 아직 아니다
        db.events.clear()
        db.manifest["2026-07-a"]["storage_verified_at"] = now - timedelta(days=13)
        assert run_selfcheck(db, lambda: st, now=now) == 0 and db.events == []

    def test_no_manifest_needs_no_storage(self):
        db = FakeDB({"2026-09-b": [make_record(1, "2026-09-b")]})
        assert run_selfcheck(db, lambda: (_ for _ in ()).throw(AssertionError("no storage"))) == 0


# ------------------------------------------------------------
# VaultDB SQL 술어 — 실행된 SQL만 모으는 가짜 커넥션
# ------------------------------------------------------------
class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))

    def fetchall(self):
        return self.conn.results.pop(0) if self.conn.results else []

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None

    rowcount = 0


class _Conn:
    def __init__(self, results=None):
        self.executed = []
        self.results = list(results or [])
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _squash(sql: str) -> str:
    return " ".join(sql.split())


class TestVaultDBSql:
    def test_require_ready_checks_manifest_table(self):
        conn = _Conn(results=[[{"t": None}]])
        with pytest.raises(VaultError, match="20260917000001_vault.sql"):
            VaultDB(conn).require_ready()
        assert conn.rollbacks == 1
        VaultDB(_Conn(results=[[{"t": "vault_manifest"}]])).require_ready()

    def test_record_page_query_is_keyset_period_bounded_with_history(self):
        conn = _Conn(results=[[]])
        start, end = period_bounds("2026-07-a")
        assert list(VaultDB(conn).iter_period_records(start, end)) == []
        sql, params = conn.executed[0]
        assert "(p.published_at, p.id) > (%s::timestamptz, %s::uuid)" in sql
        assert "ORDER BY p.published_at, p.id" in sql and "LIMIT %s" in sql
        assert params[:2] == (start, end) and params[2] == start and params[4] == vault.EXPORT_PAGE_ROWS
        # 없는 분석·정책은 null (전부 null인 객체가 아니라)
        assert "CASE WHEN a.id IS NULL THEN NULL ELSE to_jsonb(a) END" in sql
        assert "CASE WHEN pd.published_item_id IS NULL THEN NULL ELSE to_jsonb(pd) END" in sql
        assert "coalesce(s.name, '수동 등록')" in sql
        # C2 — current가 아닌 그 클러스터의 analyses 전부, 행 전체(to_jsonb), 빈 배열 기본
        squashed = _squash(sql)
        assert "'analysis_history', coalesce((" in squashed
        assert "jsonb_agg(to_jsonb(h) ORDER BY h.generated_at, h.id)" in squashed
        assert "FROM analyses h WHERE h.cluster_id = p.cluster_id" in squashed
        assert "h.id <> p.current_analysis_id" in squashed
        # is_visible로 거르지 않는다 — 그 기간 카드 전부
        assert "is_visible =" not in sql and "is_visible=" not in sql

    def test_record_page_advances_keyset(self):
        page = [{"_published_at": f"t{i}", "_id": f"id{i}", "record": {"id": f"id{i}"}} for i in range(vault.EXPORT_PAGE_ROWS)]
        conn = _Conn(results=[page, []])
        start, end = period_bounds("2026-07-a")
        got = list(VaultDB(conn).iter_period_records(start, end))
        assert len(got) == vault.EXPORT_PAGE_ROWS
        assert conn.executed[1][1][2:4] == (page[-1]["_published_at"], page[-1]["_id"])

    def test_prune_candidates_lock_rows_and_exclude_live_jobs_vaulted_skipped(self):
        conn = _Conn(results=[[]])
        start, end = period_bounds("2026-07-a")
        VaultDB(conn).prune_candidates(start, end, 200, ["skip-1"])
        sql, params = conn.executed[0]
        assert "p.current_analysis_id::text AS current_analysis_id" in sql
        assert "p.vaulted_at IS NULL" in sql
        assert "NOT (p.id = ANY(%s::uuid[]))" in sql
        assert "NOT EXISTS" in sql and "analysis_jobs j" in sql and "j.status = ANY(%s)" in sql
        assert _squash(sql).endswith("LIMIT %s FOR UPDATE OF p SKIP LOCKED")  # 경합 [29]
        assert params == (start, end, ["skip-1"], list(ACTIVE_JOB_STATUSES), 200)
        assert set(ACTIVE_JOB_STATUSES) >= {"PENDING", "RETRY", "PROCESSING"}
        # exclude_ids 생략 → 빈 배열
        VaultDB(_Conn(results=[[]])).prune_candidates(start, end, 5)

    def test_prune_batch_order_recorded_ids_only_and_single_commit(self):
        conn = _Conn(results=[[{"id": "p1", "cluster_id": "c1"}]])
        n = VaultDB(conn).prune_batch(["p1", "p2"], ["a1", "h1"])
        assert n == 1 and conn.commits == 1
        sqls = [_squash(s) for s, _ in conn.executed]
        params = [p for _, p in conn.executed]
        active = list(ACTIVE_JOB_STATUSES)
        # ① 표식이 먼저 — 살아 있는 job 없음을 문장 안에서 다시 확인, RETURNING으로 대상 확정
        assert sqls[0].startswith("UPDATE published_items p SET vaulted_at = now()")
        assert "p.vaulted_at IS NULL" in sqls[0] and "NOT EXISTS" in sqls[0] and "analysis_jobs j" in sqls[0]
        assert "RETURNING p.id::text AS id, p.cluster_id::text AS cluster_id" in sqls[0]
        assert params[0] == (["p1", "p2"], active)
        # ② 본문 NULL — 표식된 클러스터만, 행은 남긴다, C5 대표 본문 보호, 살아 있는 job 재확인
        assert sqls[1].startswith("UPDATE raw_items ri SET clean_text = NULL, raw_text = NULL")
        assert "FROM cluster_members cm WHERE cm.cluster_id = ANY(%s::uuid[])" in sqls[1]
        assert "c.representative_raw_item_id IS NOT NULL" in sqls[1]
        assert "c2.representative_raw_item_id = ri.id AND p2.vaulted_at IS NULL AND NOT (p2.id = ANY(%s::uuid[]))" in sqls[1]
        assert "j.status = ANY(%s)" in sqls[1]
        assert params[1] == (["c1"], ["c1"], ["p1"], active)
        # ③ analyses — 기록된 id만(C3), 표식된 클러스터의 것만, cluster_id 통째 삭제가 아님
        assert sqls[2].startswith("DELETE FROM analyses a WHERE a.id = ANY(%s::uuid[]) AND a.cluster_id = ANY(%s::uuid[])")
        assert "NOT EXISTS" in sqls[2]
        assert params[2] == (["a1", "h1"], ["c1"], active)
        # ④ 끝난 job만
        assert sqls[3].startswith("DELETE FROM analysis_jobs j WHERE j.cluster_id = ANY(%s::uuid[]) AND j.status = ANY(%s)")
        assert params[3] == (["c1"], list(FINISHED_JOB_STATUSES), active)
        joined = " ".join(sqls)
        assert "DELETE FROM raw_items" not in joined  # 행은 남긴다
        assert "DELETE FROM cluster_members" not in joined  # C4 — 구성원 표는 남긴다
        assert "cluster_members" not in sqls[2] and "cluster_members" not in sqls[3]

    def test_prune_batch_with_nothing_marked_touches_nothing_else(self):
        conn = _Conn(results=[[]])
        assert VaultDB(conn).prune_batch(["p1"], ["a1"]) == 0
        assert len(conn.executed) == 1 and conn.commits == 1

    def test_prunable_periods_need_both_verifications(self):
        conn = _Conn(results=[[]])
        VaultDB(conn).prunable_periods()
        sql = conn.executed[0][0]
        assert "storage_verified_at IS NOT NULL" in sql
        assert "release_verified_at IS NOT NULL" in sql and "pruned_at IS NULL" in sql

    def test_upsert_manifest_resets_verification_marks_but_not_failures(self):
        conn = _Conn()
        VaultDB(conn).upsert_manifest({
            "month": "2026-07-a", "rows": 3, "bytes": 10, "sha256": "s", "storage_path": "p",
            "sample_ids": ["a"], "analysis_ids": {"a": ["x", "y"]},
        })
        sql, params = conn.executed[0]
        assert "ON CONFLICT (month) DO UPDATE" in sql
        assert "storage_verified_at = NULL" in sql and "release_verified_at = NULL" in sql
        assert "analysis_ids = EXCLUDED.analysis_ids" in sql
        assert "verify_failures" not in sql  # C9 — 재내보내기가 실패 횟수를 지우지 않는다
        assert params[-2] == '["a"]' and json.loads(params[-1]) == {"a": ["x", "y"]} and conn.commits == 1

    def test_verify_marks(self):
        conn = _Conn()
        VaultDB(conn).mark_storage_verified("2026-07-a")
        assert "storage_verified_at = now(), verify_failures = 0" in conn.executed[0][0]
        conn = _Conn(results=[[{"verify_failures": 2}]])
        assert VaultDB(conn).bump_verify_failures("2026-07-a") == 2
        assert "verify_failures = verify_failures + 1" in conn.executed[0][0]
        assert "RETURNING verify_failures" in conn.executed[0][0] and conn.commits == 1
        assert VaultDB(_Conn(results=[[]])).bump_verify_failures("2026-07-a") == 0  # 행 없음

    def test_record_event_type_matches_check(self):
        conn = _Conn()
        VaultDB(conn).record_event("VAULT_MISMATCH", "x" * 600, {"month": "2026-07-a"})
        sql, params = conn.executed[0]
        assert "INSERT INTO operation_events" in sql and "'vault_manifest'" in sql
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", params[0])
        assert len(params[1]) == 500 and json.loads(params[2]) == {"month": "2026-07-a"}

    def test_period_sql_uses_seoul_half_month(self):
        conn = _Conn(results=[[]])
        VaultDB(conn).period_candidates()
        sql = conn.executed[0][0]
        assert "AT TIME ZONE 'Asia/Seoul'" in sql
        assert "extract(day FROM published_at AT TIME ZONE 'Asia/Seoul') <= 15" in sql
        assert "THEN '-a' ELSE '-b'" in sql
        conn = _Conn(results=[[]])
        VaultDB(conn).period_stats()
        assert "THEN '-a' ELSE '-b'" in conn.executed[0][0] and "count(vaulted_at)" in conn.executed[0][0]


# ------------------------------------------------------------
# config·cleanup 편입
# ------------------------------------------------------------
class TestSettingsAndPlan:
    def test_defaults(self):
        s = Settings()
        assert s.vault_window_days == 14  # C8
        assert s.vault_prune_enabled is False
        assert s.vault_prune_max_per_run == 3000
        assert s.supabase_url == "" and s.supabase_service_role_key == ""

    def test_env_and_app_settings(self, monkeypatch):
        for key in ("VAULT_WINDOW_DAYS", "VAULT_PRUNE_ENABLED", "VAULT_PRUNE_MAX_PER_RUN"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("NEXT_PUBLIC_SUPABASE_URL", SUPABASE_URL)
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
        monkeypatch.setenv("VAULT_PRUNE_ENABLED", "false")
        s = Settings.from_env()
        assert s.supabase_url == SUPABASE_URL and s.supabase_service_role_key == KEY
        s.apply_app_settings({"vault_window_days": 45, "vault_prune_enabled": True, "vault_prune_max_per_run": "500"})
        assert s.vault_window_days == 45
        assert s.vault_prune_enabled is False  # 환경변수 우선
        assert s.vault_prune_max_per_run == 500

    def test_daily_plan_has_vault_steps_in_order_after_existing(self):
        labels = [label for label, _ in cleanup.build_plan(object(), Settings(), [])]
        tail = labels[-4:]
        assert tail == ["raw_response(PASS)", "금고 내보내기·검증", "금고 정리", "금고 자가점검"]
        assert labels.index("gemini_calls") < labels.index("raw_response(PASS)")

    def test_raw_response_daily_respects_keep_flag(self):
        s = Settings()
        s.keep_raw_response = True
        plan = dict(cleanup.build_plan(object(), s, []))
        assert plan["raw_response(PASS)"]() == 0  # conn을 건드리지 않는다
        s.keep_raw_response = False
        conn = _Conn()
        plan = dict(cleanup.build_plan(conn, s, []))
        assert plan["raw_response(PASS)"]() == 0
        sql = conn.executed[0][0]
        assert "UPDATE analyses SET raw_response = NULL" in sql and "validation_status = 'PASS'" in sql

    def test_vault_steps_receive_conn_and_settings(self, monkeypatch):
        calls = []
        monkeypatch.setattr(vault, "export_and_verify", lambda conn, settings: calls.append(("e", conn, settings)) or 1)
        monkeypatch.setattr(vault, "prune", lambda conn, settings: calls.append(("p", conn, settings)) or 2)
        monkeypatch.setattr(vault, "selfcheck", lambda conn, settings: calls.append(("s", conn, settings)) or 0)
        conn, s = object(), Settings()
        plan = dict(cleanup.build_plan(conn, s, []))
        assert [plan[k]() for k in ("금고 내보내기·검증", "금고 정리", "금고 자가점검")] == [1, 2, 0]
        assert [c[0] for c in calls] == ["e", "p", "s"] and all(c[1] is conn and c[2] is s for c in calls)

    def test_entry_points_wrap_settings_and_apply_window_floor(self, monkeypatch, capsys):
        seen = {}
        monkeypatch.setattr(vault, "run_prune", lambda db, enabled, max_per_run: seen.update(enabled=enabled, cap=max_per_run) or 0)
        s = Settings()
        s.vault_prune_enabled = True
        s.vault_prune_max_per_run = 7
        vault.prune(object(), s)
        assert seen == {"enabled": True, "cap": 7}
        monkeypatch.setattr(
            vault, "run_export_verify",
            lambda db, factory, window_days, today=None, max_periods=3: seen.update(window=window_days) or 0,
        )
        s.vault_window_days = 5
        vault.export_and_verify(object(), s)
        assert seen["window"] == 14 and "하한" in capsys.readouterr().err  # C8


# ------------------------------------------------------------
# publish.py — published_items.kiro_axes 사본 (C7)
# ------------------------------------------------------------
class TestPublishKiroAxes:
    ANALYSIS = ArticleAnalysis(
        is_robot_related=True, display_title="산업부, 휴머노이드 실증사업 발표", one_line_summary="요약",
        category="정책", region="국내", robot_field="휴머노이드·피지컬 AI", importance="높음",
        evidence_level="강함", kiro_relevance="직접", kiro_relevance_axes=["정책·전략", "R&D 기획"],
        verified_facts=["사실"], kiro_implication="시사점",
    )

    def test_insert_and_conflict_update_carry_kiro_axes(self, monkeypatch):
        monkeypatch.setattr(publish, "_merge_into_existing", lambda conn, cluster_id, analysis: None)
        rep = {"url": "https://x/1", "published_at": None, "title": "t", "source_name": "산업부", "member_count": 1}
        conn = _Conn(results=[[rep], [{"id": "pub-1"}]])
        assert publish.publish_analysis(conn, "c1", "a1", self.ANALYSIS) == "pub-1"
        sql, params = next((s, p) for s, p in conn.executed if "INSERT INTO published_items" in s)
        cols = _squash(sql.split("VALUES")[0])
        assert "kiro_relevance, kiro_axes, source_published_at" in cols
        assert "kiro_axes = EXCLUDED.kiro_axes" in sql
        assert cols.count(",") + 1 == sql.split("VALUES")[1].split("ON CONFLICT")[0].count("%s")
        axes_param = params[cols.replace("INSERT INTO published_items (", "").split(",").index(" kiro_axes")]
        assert json.loads(axes_param) == ["정책·전략", "R&D 기획"]


# ------------------------------------------------------------
# generate_brief.py — 금고로 옮긴 기간은 재생성하지 않는다 ([14])
# ------------------------------------------------------------
class TestBriefVaultGuard:
    def test_count_vaulted_cards_uses_same_period_predicate(self):
        conn = _Conn(results=[[{"n": 3}]])
        assert generate_brief.count_vaulted_cards(conn, date(2026, 7, 6), date(2026, 7, 12)) == 3
        sql, params = conn.executed[0]
        assert "vaulted_at IS NOT NULL" in sql
        assert "coalesce(p.source_published_at, p.published_at) >= %s" in sql
        start_ts, end_ts = generate_brief.kst_bounds(date(2026, 7, 6), date(2026, 7, 12))
        assert params == (start_ts, end_ts)
        assert "재생성할 수 없습니다" in generate_brief.VAULTED_PERIOD_MESSAGE

    def test_main_stops_with_exit_0_before_generating(self, monkeypatch):
        conn = _Conn(results=[[{"n": 2}]])
        monkeypatch.setattr(generate_brief, "connect", lambda url: conn)
        monkeypatch.setattr(generate_brief, "load_app_settings", lambda c: {})
        monkeypatch.setattr(generate_brief.Settings, "require", lambda self, *names: None)
        monkeypatch.setattr(generate_brief, "UsageRecorder", lambda c, name: SimpleNamespace(
            counts={}, finish=lambda status: None))
        monkeypatch.setattr(generate_brief, "select_brief_targets",
                            lambda *a, **k: pytest.fail("금고 기간은 후보 선별까지 가면 안 된다"))
        conn.close = lambda: None
        monkeypatch.setenv("BRIEF_WEEK_START", "2026-07-06")
        monkeypatch.delenv("BRIEF_FORCE", raising=False)
        assert generate_brief.main() == 0
        assert any("vaulted_at IS NOT NULL" in s for s, _ in conn.executed)
        assert conn.rollbacks == 1


# ------------------------------------------------------------
# 마이그레이션 파일 — 계약 고정 (적용은 운영자가 BEGIN…ROLLBACK 검증 뒤)
# ------------------------------------------------------------
class TestMigrationContract:
    SQL = MIGRATION.read_text(encoding="utf-8")

    def test_half_month_check_and_manifest_columns(self):
        assert r"CHECK (month ~ '^\d{4}-\d{2}-[ab]$')" in self.SQL
        assert "DROP CONSTRAINT IF EXISTS vault_manifest_month_check" in self.SQL
        assert "analysis_ids         jsonb NOT NULL DEFAULT '{}'::jsonb" in self.SQL
        assert "verify_failures      int NOT NULL DEFAULT 0" in self.SQL
        assert "ADD COLUMN IF NOT EXISTS analysis_ids" in self.SQL and "ADD COLUMN IF NOT EXISTS verify_failures" in self.SQL

    def test_kiro_axes_column_backfill_and_fk(self):
        assert "ADD COLUMN IF NOT EXISTS kiro_axes jsonb" in self.SQL
        squashed = _squash(self.SQL)
        assert ("UPDATE public.published_items p SET kiro_axes = a.kiro_relevance_axes "
                "FROM public.analyses a WHERE a.id = p.current_analysis_id") in squashed
        assert "REFERENCES public.analyses (id) ON DELETE SET NULL" in self.SQL
        assert "ADD COLUMN IF NOT EXISTS vaulted_at timestamptz" in self.SQL
        assert "('vault_window_days', '14'" in self.SQL
