"""키워드 규칙 로더 — keyword_rules 테이블 → 배치 모듈 (2026-09-08, 관리 기능).

세 곳의 키워드(뉴스 로컬 필터·보고서 계층·R&D 공고)를 운영자가 코드 수정
없이 /admin/keywords 화면에서 고칠 수 있도록 DB에서 읽는다.

원칙
- DB에 못 붙거나, 조회가 실패하거나, 규칙이 0건이어도 배치는 멈추지 않는다.
  이 모듈은 빈 dict를 돌려주고, 호출자(local_filter·classify·collect_rnd)가
  코드/JSON에 내장된 기본값으로 폴백한다.
- 프로세스 안에서는 rule_set당 1회만 조회한다 (단순 캐시). 배치는 짧게 살고
  끝나므로 갱신은 다음 실행부터 반영된다.
- 잘못된 정규식은 그 규칙 하나만 건너뛴다 (stderr 경고) — 화면(admin/keywords
  actions.ts)이 저장 단계에서 JS 문법·파이썬에 없는 표기·중첩 반복을 막지만,
  두 정규식 엔진이 완전히 같지는 않다.
- 규칙 하나가 배치를 세우지 못하게 한다: 되짚기가 폭발하는 정규식((a+)+ 류)은
  아예 컴파일하지 않고, 그래도 오래 걸리는 검색은 시간 제한으로 끊는다.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

# 파이썬 표준 re에는 검색 시간 제한이 없어, 되짚기가 폭발하는 정규식 하나가
# 기사 한 건에서 배치 전체를 멈춰 세운다(collect.yml timeout-minutes 15). regex
# 모듈은 search(timeout=)을 지원하고 trafilatura→htmldate→dateparser로 이미 함께
# 설치되므로, 있으면 쓰고 없으면 아래 구조 검사만으로 막는다 (새 의존성은 만들지 않는다).
try:
    import regex as _regex_module
except ImportError:  # pragma: no cover — regex 없이도 배치는 돌아야 한다
    _regex_module = None

if _regex_module is not None:
    try:  # timeout 인자는 regex 2022.9부터 — 없으면 쓰지 않는다 (TypeError 방지)
        _regex_module.compile("a").search("a", timeout=1.0)
    except TypeError:  # pragma: no cover — 오래된 regex 버전
        _regex_module = None

# 규칙 1건이 텍스트 1건에서 쓸 수 있는 시간. 넘기면 그 규칙은 이번 실행 동안 빼둔다.
REGEX_TIMEOUT_SECONDS = 1.0

# rule_set → 허용 kind (테이블 주석과 동일 — 화면 actions.ts의 목록과 맞춘다)
RULE_SETS: dict[str, tuple[str, ...]] = {
    "news_filter": ("robot", "strong", "exclude", "event_only"),
    "report_tier": ("core", "tool"),
    "rnd_keywords": ("keyword", "pattern"),
}

# {kind: [(term, is_regex), ...]} — 활성(enabled) 규칙만
Rules = dict[str, list[tuple[str, bool]]]

_cache: dict[str, Rules] = {}

# 시간 제한을 넘긴 정규식(term). 같은 규칙을 남은 기사마다 다시 시도하면 배치가
# 사실상 멈추므로 이번 실행에서는 불일치로 취급한다.
_timed_out_terms: set[str] = set()


def _warn(message: str) -> None:
    print(f"[keyword_rules] {message}", file=sys.stderr)


def _rollback_quietly(conn) -> None:
    """실패한 조회가 트랜잭션을 깨뜨린 상태로 두지 않는다 (psycopg는 이후 쿼리를
    전부 거부한다). 호출 시점은 배치 시작 직후라 되돌릴 작업이 없다."""
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001 — 연결 자체가 죽은 경우
        pass


def load_rules(conn, rule_set: str) -> Rules:
    """rule_set의 활성 규칙을 {kind: [(term, is_regex)]}로 돌려준다.

    조회 실패·0건이면 빈 dict — 호출자가 기본값으로 폴백한다.
    성공한 결과는 프로세스 안에서 캐시된다 (실패는 캐시하지 않는다).
    """
    if rule_set not in RULE_SETS:
        raise ValueError(f"알 수 없는 rule_set: {rule_set!r}")
    cached = _cache.get(rule_set)
    if cached is not None:
        return cached

    out: Rules = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT kind, term, is_regex
                FROM keyword_rules
                WHERE rule_set = %s AND enabled
                ORDER BY kind, created_at, term
                """,
                (rule_set,),
            )
            rows = cur.fetchall()
    except Exception as e:  # noqa: BLE001 — DB 문제로 배치를 세우지 않는다
        _warn(f"{rule_set} 규칙 조회 실패 — 내장 기본값 사용: {e}")
        _rollback_quietly(conn)
        return {}

    for row in rows:
        term = str(row["term"]).strip()
        if not term:
            continue
        out.setdefault(str(row["kind"]), []).append((term, bool(row["is_regex"])))

    _cache[rule_set] = out
    return out


def load_setting_int(conn, key: str, default: int) -> int:
    """app_settings의 정수 설정 하나 (jsonb). 없거나 실패하면 default."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
            row = cur.fetchone()
    except Exception as e:  # noqa: BLE001
        _warn(f"{key} 설정 조회 실패 — 기본값 {default} 사용: {e}")
        _rollback_quietly(conn)
        return default
    if not row:
        return default
    value = row["value"]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            pass
    try:
        return int(value)
    except (TypeError, ValueError):
        _warn(f"{key} 값이 정수가 아님({value!r}) — 기본값 {default} 사용")
        return default


def clear_cache() -> None:
    """테스트용 — 프로세스 캐시를 비운다."""
    _cache.clear()
    _timed_out_terms.clear()


# ------------------------------------------------------------
# 용어 매처 — 세 모듈이 같은 방식으로 일치 검사를 한다
# ------------------------------------------------------------
@dataclass(frozen=True)
class Term:
    """규칙 한 건. 소문자로 바꾼 텍스트를 받아 일치한 문자열을 돌려준다.

    - 일반 용어: 부분 문자열 일치 → 용어 자체(소문자)를 반환
    - 정규식: IGNORECASE 검색 → 실제 일치한 문자열을 반환
    """

    term: str
    is_regex: bool
    # re.Pattern 또는 regex.Pattern — search()/group() 쓰임새가 같다
    pattern: re.Pattern | None = None

    @property
    def needle(self) -> str:
        return self.term.lower()

    def find(self, text_lower: str) -> str | None:
        pattern = self.pattern
        if pattern is not None:
            if self.term in _timed_out_terms:
                return None
            try:
                # timeout은 regex 모듈 패턴에만 있다 (표준 re 패턴이면 TypeError)
                if isinstance(pattern, re.Pattern):
                    m = pattern.search(text_lower)
                else:
                    m = pattern.search(text_lower, timeout=REGEX_TIMEOUT_SECONDS)
            except TimeoutError:
                _timed_out_terms.add(self.term)
                _warn(
                    f"정규식이 {REGEX_TIMEOUT_SECONDS}초를 넘겨 이번 실행에서 뺀다 "
                    f"— /admin/keywords에서 이 규칙을 끄거나 고칠 것: {self.term!r}"
                )
                return None
            return m.group(0) if m else None
        needle = self.needle
        return needle if needle and needle in text_lower else None


def has_nested_quantifier(pattern: str) -> bool:
    """반복 안에 반복이 있는 정규식인가 — (a+)+ · (\\w+\\s?)* · (.*a){20} 류.

    일치하지 않는 긴 본문에서 되짚기가 지수적으로 늘어나 수집·분석이 통째로
    멈춘다(파이썬 re에는 시간 제한이 없다). 화면(admin/keywords actions.ts)도
    같은 규칙으로 저장을 막으므로 두 곳의 판단이 어긋나지 않아야 한다.
    """
    stack: list[bool] = []  # 바깥 그룹들이 지금까지 본 반복 여부
    in_class = False  # [...] 안의 +*{ 는 반복이 아니라 글자다
    quant_seen = False
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":  # 이스케이프된 글자는 통째로 건너뛴다
            i += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
        elif c == "[":
            in_class = True
        elif c == "(":
            stack.append(quant_seen)
            quant_seen = False
        elif c == ")":
            inner = quant_seen
            parent = stack.pop() if stack else False
            repeated = pattern[i + 1 : i + 2] in ("*", "+", "{")
            if inner and repeated:
                return True
            quant_seen = parent or inner or repeated
        elif c in "*+{":
            quant_seen = True
        i += 1
    return False


def _compile_pattern(term: str):
    """정규식 하나를 컴파일한다. regex 모듈이 있으면 시간 제한을 쓸 수 있는 쪽으로."""
    if _regex_module is not None:
        return _regex_module.compile(term, _regex_module.IGNORECASE)
    return re.compile(term, re.IGNORECASE)


_PATTERN_ERRORS: tuple[type[Exception], ...] = (
    (re.error,) if _regex_module is None else (re.error, _regex_module.error)
)


def compile_terms(pairs: list[tuple[str, bool]], *, label: str = "") -> list[Term]:
    """(term, is_regex) 목록을 Term으로. 잘못된 정규식은 경고만 내고 건너뛴다."""
    out: list[Term] = []
    for term, is_regex in pairs:
        term = (term or "").strip()
        if not term:
            continue
        if is_regex:
            if has_nested_quantifier(term):
                _warn(
                    f"{label or '규칙'} 중첩 반복 정규식 — 배치를 멈출 수 있어 "
                    f"건너뜀: {term!r}"
                )
                continue
            try:
                out.append(Term(term, True, _compile_pattern(term)))
            except _PATTERN_ERRORS as e:
                _warn(f"{label or '규칙'} 정규식 컴파일 실패 — 건너뜀: {term!r} ({e})")
        else:
            out.append(Term(term, False))
    return out


def plain(terms: list[str]) -> list[tuple[str, bool]]:
    """코드 상수(일반 용어 목록)를 (term, False) 쌍으로."""
    return [(t, False) for t in terms]


def regex(patterns: list[str]) -> list[tuple[str, bool]]:
    """코드 상수(정규식 목록)를 (term, True) 쌍으로."""
    return [(p, True) for p in patterns]
