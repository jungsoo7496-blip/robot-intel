"""보고서 수집 순수 로직 테스트 — prefilter·dedup·pub_year 파싱·검증."""

from kiro_batch.analyze_reports import validate_item
from kiro_batch.reports.classify import (
    keyword_tier,
    matched_keywords,
    prefilter_pass,
)
from kiro_batch.reports.dedup import (
    analysis_input_hash,
    document_fingerprint,
    normalize_title,
)
from kiro_batch.reports.nanet import _parse_records, _split_title_author
from kiro_batch.reports.point import _clean_title, _parse_pub_year


class TestPrefilter:
    def test_robot_title_is_core(self):
        assert keyword_tier("주요국 AI 로봇 경쟁력 분석과 시사점") == "CORE"

    def test_embodied_ai_is_core(self):
        assert keyword_tier("Embodied AI 산업 전망") == "CORE"

    def test_drone_and_ai_are_tool_tier(self):
        # 사용자 지시(2026-08-08): 드론·AI는 로봇의 도구 — 수집하되 후순위
        assert keyword_tier("인공지능이 미래 전쟁에 미치는 영향") == "TOOL"
        assert keyword_tier("스마트팩토리 자동화와 드론 산업 동향") == "TOOL"
        assert prefilter_pass("드론 물류 배송 실증 결과")

    def test_unrelated_excluded(self):
        assert keyword_tier("영국 암 치료 계획 요약서") is None
        assert not prefilter_pass("노인돌봄서비스 인력의 전망과 정책방향")

    def test_abstract_can_rescue(self):
        assert keyword_tier(
            "제조혁신 실태조사", "협동로봇 도입 사례를 중심으로 분석"
        ) == "CORE"

    def test_matched_keywords_sorted_unique(self):
        kws = matched_keywords("휴머노이드 로봇과 humanoid robot")
        assert "휴머노이드" in kws and "robot" in kws
        assert kws == sorted(set(kws))


class TestDedup:
    def test_normalize_strips_subtitle_and_symbols(self):
        a = normalize_title("로봇산업 실태조사 (2025년 기준)")
        b = normalize_title("로봇산업  실태조사")
        assert a == b

    def test_fingerprint_same_report_across_sources(self):
        f1 = document_fingerprint("로봇산업 실태조사", "한국로봇산업진흥원", 2025)
        f2 = document_fingerprint("로봇산업 실태조사 (요약)", "한국로봇산업진흥원(KIRIA)", 2025)
        assert f1 == f2

    def test_fingerprint_differs_by_year(self):
        # 잘못 합치는 것보다 중복 2개가 낫다 — 연도가 다르면 다른 문서
        f1 = document_fingerprint("로봇산업 실태조사", "진흥원", 2024)
        f2 = document_fingerprint("로봇산업 실태조사", "진흥원", 2025)
        assert f1 != f2

    def test_input_hash_changes_with_abstract(self):
        h1 = analysis_input_hash("t", "i", "abstract A", "chamgo", ["로봇"], "2025-01-01")
        h2 = analysis_input_hash("t", "i", "abstract B", "chamgo", ["로봇"], "2025-01-01")
        assert h1 != h2


class TestPointParsing:
    def test_pub_year_full_date(self):
        assert _parse_pub_year("20240923") == (2024, "2024-09-23")

    def test_pub_year_month_only_no_fabricated_day(self):
        year, date_iso = _parse_pub_year("202412")
        assert year == 2024 and date_iso is None

    def test_pub_year_year_only(self):
        assert _parse_pub_year("2026") == (2026, None)

    def test_pub_year_garbage(self):
        assert _parse_pub_year("") == (None, None)
        assert _parse_pub_year("발행일 미상") == (None, None)

    def test_clean_title_strips_highlight(self):
        assert _clean_title("주요국 <font color='red'>로봇</font> 정책") == "주요국 로봇 정책"


class TestNanetParsing:
    # 국회도서관 OpenAPI 가이드 PDF의 응답 예시 그대로
    SAMPLE = """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<response><record>
<item><name>제어번호</name><value>MONO1201103155</value></item>
<item><name>발행년</name><value>2010</value></item>
<item><name>발행자</name><value>쿰란출판사</value></item>
<item><name>자료명/저자사항</name><value>생명, 하나님의 주권 :인간 배아복제에 대한 신학적 답변 /김기철 지음</value></item>
<item><name>DB</name><value>일반도서</value></item>
</record><total>23,400</total></response>"""

    def test_parse_records_and_total(self):
        records, total = _parse_records(self.SAMPLE)
        assert total == 23400  # 쉼표 포함 숫자
        assert records[0]["제어번호"] == "MONO1201103155"
        assert records[0]["발행년"] == "2010"

    def test_split_title_author(self):
        title, authors = _split_title_author(
            "생명, 하나님의 주권 :인간 배아복제에 대한 신학적 답변 /김기철 지음"
        )
        assert title.startswith("생명, 하나님의 주권")
        assert authors == ["김기철"]


class TestValidateItem:
    BASE = {
        "robot_relevance": "DIRECT",
        "relevance_reason": "핵심 주제",
        "report_type": "정책·전략",
        "primary_robot_field": "휴머노이드·피지컬 AI",
        "robot_fields": ["휴머노이드·피지컬 AI"],
        "summary": "요약 문장.",
        "kiro_relevance": "직접",
        "kiro_relevance_axes": ["정책·전략"],
        "kiro_reason": "이유",
        "keywords": ["로봇"],
        "limitations": "",
    }

    def test_valid_item_passes(self):
        item = validate_item(dict(self.BASE))
        assert item is not None and item["robot_relevance"] == "DIRECT"

    def test_bad_relevance_rejected(self):
        assert validate_item({**self.BASE, "robot_relevance": "MAYBE"}) is None

    def test_empty_summary_rejected(self):
        assert validate_item({**self.BASE, "summary": " "}) is None

    def test_unknown_enum_values_coerced(self):
        item = validate_item(
            {
                **self.BASE,
                "report_type": "이상한유형",
                "primary_robot_field": "미지분야",
                "kiro_relevance": "매우높음",
                "kiro_relevance_axes": ["없는축", "R&D 기획"],
            }
        )
        assert item["report_type"] == "기타"
        assert item["primary_robot_field"] == "기타"
        assert item["kiro_relevance"] == "낮음"
        assert item["kiro_relevance_axes"] == ["R&D 기획"]

    def test_keywords_capped_at_8(self):
        item = validate_item({**self.BASE, "keywords": [str(i) for i in range(20)]})
        assert len(item["keywords"]) == 8
