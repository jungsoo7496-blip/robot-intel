-- ============================================================
-- 0009: 네이버 뉴스 검색 API 수집 방식 추가
-- ============================================================

ALTER TABLE public.sources
  DROP CONSTRAINT sources_fetch_method_check;

ALTER TABLE public.sources
  ADD CONSTRAINT sources_fetch_method_check
  CHECK (fetch_method IN ('RSS', 'LIST_PAGE', 'PREDEFINED', 'NAVER_API'));
