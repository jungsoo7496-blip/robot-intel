-- ============================================================
-- 0011: KIRO 관련성 구조화 필드 (스키마 v1.1, 브로슈어 공개 컨텍스트)
-- 기존 분석 이력은 삭제·변경하지 않는다. 새 분석부터 채워진다.
-- ============================================================

ALTER TABLE public.analyses
  ADD COLUMN kiro_relevance_axes jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN kiro_relevance_reason text,
  ADD COLUMN kiro_watchpoints jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN kiro_context_version text;

-- 향후 업무축 필터 탐색용 (예: 실증·시험평가 관련 동향)
CREATE INDEX analyses_kiro_axes_gin_idx
ON public.analyses USING gin (kiro_relevance_axes jsonb_path_ops);
