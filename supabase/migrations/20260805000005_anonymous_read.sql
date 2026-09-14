-- ============================================================
-- 0005: 익명 열람 허용 (docs/DECISIONS.md D-001)
-- 열람은 로그인 없이, 관리는 마스터 OPERATOR 계정으로만.
-- 운영 테이블(sources, raw_items, 큐, 사용량 등)은 계속 차단된다.
-- ============================================================

-- ------------------------------------------------------------
-- 게시 콘텐츠: 익명 포함 전체 열람 허용
-- ------------------------------------------------------------
DROP POLICY published_items_select ON public.published_items;
CREATE POLICY published_items_select
ON public.published_items FOR SELECT
USING (is_visible = true OR public.is_operator());

DROP POLICY analyses_select ON public.analyses;
CREATE POLICY analyses_select
ON public.analyses FOR SELECT
USING (true);

DROP POLICY policy_details_select ON public.policy_details;
CREATE POLICY policy_details_select
ON public.policy_details FOR SELECT
USING (true);

DROP POLICY content_clusters_select ON public.content_clusters;
CREATE POLICY content_clusters_select
ON public.content_clusters FOR SELECT
USING (true);

-- ------------------------------------------------------------
-- 브리프: 발행본은 익명 열람, 초안·실패본은 운영자만
-- ------------------------------------------------------------
DROP POLICY brief_periods_select ON public.brief_periods;
CREATE POLICY brief_periods_select
ON public.brief_periods FOR SELECT
USING (true);

DROP POLICY briefs_select ON public.briefs;
CREATE POLICY briefs_select
ON public.briefs FOR SELECT
USING (status = 'PUBLISHED' OR public.is_operator());

DROP POLICY brief_items_select ON public.brief_items;
CREATE POLICY brief_items_select
ON public.brief_items FOR SELECT
USING (true);

-- ------------------------------------------------------------
-- 오류 신고: 익명 제출 허용 (reported_by nullable)
-- ------------------------------------------------------------
ALTER TABLE public.error_reports
  ALTER COLUMN reported_by DROP NOT NULL;

DROP POLICY error_reports_insert ON public.error_reports;
CREATE POLICY error_reports_insert
ON public.error_reports FOR INSERT
WITH CHECK (reported_by IS NULL OR reported_by = auth.uid());

DROP POLICY error_reports_select ON public.error_reports;
CREATE POLICY error_reports_select
ON public.error_reports FOR SELECT
USING (
  (reported_by IS NOT NULL AND reported_by = auth.uid())
  OR public.is_operator()
);
