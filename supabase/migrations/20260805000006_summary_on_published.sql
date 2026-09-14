-- ============================================================
-- 0006: 목록 화면에 AI 요약 우선 표시 (사용자 피드백)
-- published_items에 요약·시사점 발췌를 비정규화해
-- 목록 조회 시 analyses 조인 없이 바로 노출한다.
-- ============================================================

ALTER TABLE public.published_items
  ADD COLUMN one_line_summary text,
  ADD COLUMN kiro_implication_excerpt text;

-- 기존 게시물 백필
UPDATE public.published_items p
SET one_line_summary = a.one_line_summary,
    kiro_implication_excerpt = left(a.kiro_implication, 200)
FROM public.analyses a
WHERE a.id = p.current_analysis_id;
