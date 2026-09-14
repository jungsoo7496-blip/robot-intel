-- 0018: 검색 제목 우선순위를 DB 단계로 (외부 리뷰 P2-1)
--
-- 기존에는 최신순 20건을 가져온 뒤 그 페이지 안에서만 제목 일치를 위로
-- 올려서, 제목 일치 결과가 2페이지에 있으면 본문 일치보다 뒤로 밀렸다.
-- 전체 결과 기준 "제목 일치 → 최신" 순으로 정렬한 뒤 페이지를 자른다.
-- SECURITY INVOKER — published_items RLS(익명은 게시 범위)가 그대로 적용된다.

CREATE FUNCTION public.search_published_items(
  q text,
  page_limit integer,
  page_offset integer
)
RETURNS SETOF public.published_items
LANGUAGE sql
STABLE
AS $$
  SELECT *
  FROM public.published_items
  WHERE is_visible = true
    AND search_text ILIKE '%' || q || '%'
  ORDER BY (title ILIKE '%' || q || '%') DESC, display_date DESC
  LIMIT page_limit OFFSET page_offset
$$;

GRANT EXECUTE ON FUNCTION public.search_published_items(text, integer, integer)
  TO anon, authenticated;
