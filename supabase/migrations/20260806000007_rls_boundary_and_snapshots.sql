-- ============================================================
-- 0007: 익명 RLS 경계 강화 + 수집원 UNIQUE + 브리프 스냅샷
-- 외부 리뷰(P0-1, P0-5, P1-6) 반영.
--
-- 원칙: 익명에게는 "화면에 게시된 것"만 보인다.
--  - analyses: 공개 게시물의 현재 분석만 (과거 버전·숨김 게시물 분석 차단)
--  - policy_details: 공개 게시물 연결분만
--  - content_clusters: 공개 게시물 연결분만
--  - brief_periods / brief_items: 발행 브리프 관련만
-- ============================================================

-- ------------------------------------------------------------
-- 익명 열람 범위를 게시 범위로 제한
-- ------------------------------------------------------------
DROP POLICY analyses_select ON public.analyses;
CREATE POLICY analyses_select
ON public.analyses FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.published_items p
    WHERE p.current_analysis_id = analyses.id
      AND p.is_visible = true
  )
  OR public.is_operator()
);

DROP POLICY policy_details_select ON public.policy_details;
CREATE POLICY policy_details_select
ON public.policy_details FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.published_items p
    WHERE p.id = policy_details.published_item_id
      AND p.is_visible = true
  )
  OR public.is_operator()
);

DROP POLICY content_clusters_select ON public.content_clusters;
CREATE POLICY content_clusters_select
ON public.content_clusters FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.published_items p
    WHERE p.cluster_id = content_clusters.id
      AND p.is_visible = true
  )
  OR public.is_operator()
);

DROP POLICY brief_periods_select ON public.brief_periods;
CREATE POLICY brief_periods_select
ON public.brief_periods FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.briefs b
    WHERE b.brief_period_id = brief_periods.id
      AND b.status = 'PUBLISHED'
  )
  OR public.is_operator()
);

DROP POLICY brief_items_select ON public.brief_items;
CREATE POLICY brief_items_select
ON public.brief_items FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.briefs b
    WHERE b.id = brief_items.brief_id
      AND b.status = 'PUBLISHED'
  )
  OR public.is_operator()
);

-- ------------------------------------------------------------
-- 수집원 시드의 idempotent 처리를 위한 UNIQUE (P0-5)
-- ------------------------------------------------------------
ALTER TABLE public.sources
  ADD CONSTRAINT sources_url_key UNIQUE (url);

-- ------------------------------------------------------------
-- 브리프 부록 스냅샷 (P1-6): 발행 당시 상태를 brief_items에 보존.
-- 이후 재분석·숨김이 과거 브리프 본문을 바꾸지 못한다 (FR-013).
-- ------------------------------------------------------------
ALTER TABLE public.brief_items
  ADD COLUMN analysis_id_snapshot uuid,
  ADD COLUMN title_snapshot text,
  ADD COLUMN summary_snapshot text,
  ADD COLUMN facts_snapshot jsonb,
  ADD COLUMN source_name_snapshot text,
  ADD COLUMN source_url_snapshot text;

-- 기존 브리프 백필 (현재 시점 데이터 기준 — 이후부터는 발행 시점 고정)
UPDATE public.brief_items bi
SET analysis_id_snapshot = p.current_analysis_id,
    title_snapshot = p.title,
    summary_snapshot = p.one_line_summary,
    facts_snapshot = a.verified_facts,
    source_name_snapshot = p.representative_source_name,
    source_url_snapshot = p.representative_url
FROM public.published_items p
JOIN public.analyses a ON a.id = p.current_analysis_id
WHERE p.id = bi.published_item_id;
