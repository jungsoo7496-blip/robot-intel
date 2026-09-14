-- ============================================================
-- 0003: 인덱스와 검색 컬럼
-- 관련 설계: design v0.3 §11 / tasks v0.2 §2.4
-- ============================================================

-- ------------------------------------------------------------
-- 작업 큐 조회 (설계 §9.4)
-- SELECT ... WHERE status IN (...) AND available_at <= now()
-- ORDER BY priority, created_at FOR UPDATE SKIP LOCKED
-- ------------------------------------------------------------
CREATE INDEX analysis_jobs_queue_idx
ON public.analysis_jobs (priority ASC, created_at ASC)
WHERE status IN ('PENDING', 'RETRY', 'DEFERRED');

CREATE INDEX analysis_jobs_stale_lock_idx
ON public.analysis_jobs (locked_at)
WHERE status = 'PROCESSING';

-- ------------------------------------------------------------
-- 한국어 부분 문자열 검색 (설계 §11, FR-011)
-- ------------------------------------------------------------
CREATE INDEX published_items_search_text_trgm_idx
ON public.published_items
USING gin (search_text gin_trgm_ops);

CREATE INDEX published_items_title_trgm_idx
ON public.published_items
USING gin (title gin_trgm_ops);

-- ------------------------------------------------------------
-- 최신 동향 목록·필터 (FR-008)
-- ------------------------------------------------------------
CREATE INDEX published_items_visible_published_idx
ON public.published_items (published_at DESC)
WHERE is_visible = true;

CREATE INDEX published_items_category_idx    ON public.published_items (category);
CREATE INDEX published_items_region_idx      ON public.published_items (region);
CREATE INDEX published_items_robot_field_idx ON public.published_items (robot_field);
CREATE INDEX published_items_importance_idx  ON public.published_items (importance);

-- ------------------------------------------------------------
-- 정책·R&D 필터 (FR-010)
-- ------------------------------------------------------------
CREATE INDEX policy_details_ministries_gin_idx
ON public.policy_details USING gin (ministries);

CREATE INDEX policy_details_organizations_gin_idx
ON public.policy_details USING gin (organizations);

CREATE INDEX policy_details_period_idx
ON public.policy_details (project_start_date, project_end_date);

CREATE INDEX policy_details_budget_idx
ON public.policy_details (budget_amount_krw);

CREATE INDEX policy_details_status_idx
ON public.policy_details (announcement_status);

-- ------------------------------------------------------------
-- 수집·클러스터 조회
-- ------------------------------------------------------------
CREATE INDEX raw_items_source_idx      ON public.raw_items (source_id, fetched_at DESC);
CREATE INDEX raw_items_filter_idx      ON public.raw_items (filter_status)
  WHERE filter_status IN ('PENDING', 'LOW_PRIORITY');
CREATE INDEX raw_items_content_hash_idx ON public.raw_items (content_hash);
CREATE INDEX source_runs_source_idx    ON public.source_runs (source_id, started_at DESC);
CREATE INDEX cluster_members_cluster_idx ON public.cluster_members (cluster_id);
CREATE INDEX analyses_cluster_idx      ON public.analyses (cluster_id, generated_at DESC);

-- ------------------------------------------------------------
-- 브리프·운영
-- ------------------------------------------------------------
CREATE INDEX briefs_period_idx         ON public.briefs (brief_period_id, version DESC);
CREATE INDEX brief_items_brief_idx     ON public.brief_items (brief_id, section, display_order);
CREATE INDEX error_reports_status_idx  ON public.error_reports (status, created_at DESC);
CREATE INDEX operation_events_time_idx ON public.operation_events (created_at DESC);
CREATE INDEX workflow_usage_name_idx   ON public.workflow_usage (workflow_name, started_at DESC);

-- Gemini 일일 쿼터 집계 (PT 날짜 기준, 설계 §7.1)
CREATE INDEX gemini_calls_quota_idx
ON public.gemini_calls (model_name, quota_date_pt);
