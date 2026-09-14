-- ============================================================
-- 0008: 중복 병합 지원 + 구글뉴스 재디코딩 방지 + 신고 스팸 차단
-- (사용자 피드백: 동일 사건 4건 중복 / 외부 리뷰 2차)
-- ============================================================

-- 애그리게이터 항목 GUID — 이미 처리한 구글뉴스 링크의 재디코딩 방지
ALTER TABLE public.raw_items
  ADD COLUMN aggregator_guid text;

CREATE INDEX raw_items_aggregator_guid_idx
ON public.raw_items (aggregator_guid)
WHERE aggregator_guid IS NOT NULL;

-- 게시물 제목 유사도 병합 조회용 (pg_trgm similarity)
-- published_items.title trigram 인덱스는 0003에서 이미 생성됨.

-- 익명 오류 신고 직접 INSERT 차단: 신고는 서버 액션(service role)만
-- 사용하므로 anon 키를 통한 자동화 스팸 경로를 닫는다 (외부 리뷰).
DROP POLICY error_reports_insert ON public.error_reports;
