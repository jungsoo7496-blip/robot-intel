-- 0014: 최신순 정렬을 원문 발행일 기준으로 (사용자 피드백 2026-08-07)
--
-- 문제: 목록 "최신순"이 게시 시각(published_at) 기준이라, 분석 배치가
-- 키워드 뭉치(네이버 로봇 100건 → 휴머노이드 78건 …) 단위로 게시하면
-- 특정 키워드·수집원이 목록 상단을 통째로 점령한다.
-- 카드에 표시하는 날짜는 이미 원문 발행일(source_published_at)이므로
-- 정렬도 같은 기준으로 통일한다. 원문 발행일 미상이면 게시 시각으로 대체.

ALTER TABLE public.published_items
  ADD COLUMN display_date timestamptz GENERATED ALWAYS AS (
    coalesce(source_published_at, published_at)
  ) STORED;

CREATE INDEX published_items_display_date_idx
  ON public.published_items (display_date DESC)
  WHERE is_visible;
