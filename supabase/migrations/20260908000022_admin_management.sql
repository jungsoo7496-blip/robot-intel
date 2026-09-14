-- 운영 관리 기능 기반 (2026-09-08, feature/admin-management)
-- 1) keyword_rules — 로컬 필터·보고서 계층·R&D 공고 키워드를 DB로 (코드 수정 없이 조정)
-- 2) admin_db_stats() — 데이터 관리 화면용 용량·적체 지표 (서비스 롤만 호출)
-- 3) 보존 정책 app_settings 기본값
-- 전부 추가(additive)만 — 기존 데이터·동작에 영향 없음.

-- ------------------------------------------------------------
-- 1. keyword_rules
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.keyword_rules (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  -- 규칙 집합: news_filter(뉴스 로컬 필터) / report_tier(보고서 CORE·TOOL) / rnd_keywords(R&D 공고)
  rule_set    text NOT NULL CHECK (rule_set IN ('news_filter', 'report_tier', 'rnd_keywords')),
  -- 종류: news_filter → robot|strong|exclude|event_only
  --       report_tier → core|tool
  --       rnd_keywords → keyword|pattern
  kind        text NOT NULL,
  term        text NOT NULL,
  is_regex    boolean NOT NULL DEFAULT false,
  enabled     boolean NOT NULL DEFAULT true,
  note        text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (rule_set, kind, term)
);

CREATE INDEX IF NOT EXISTS keyword_rules_set_kind_idx
  ON public.keyword_rules (rule_set, kind) WHERE enabled;

ALTER TABLE public.keyword_rules ENABLE ROW LEVEL SECURITY;

-- 운영자만 읽고 쓴다 (배치는 서비스 롤/DB URL로 접근 — RLS 미적용)
DROP POLICY IF EXISTS keyword_rules_operator ON public.keyword_rules;
CREATE POLICY keyword_rules_operator
ON public.keyword_rules FOR ALL
USING (
  EXISTS (
    SELECT 1 FROM public.profiles p
    WHERE p.id = auth.uid() AND p.role = 'OPERATOR'
  )
)
WITH CHECK (
  EXISTS (
    SELECT 1 FROM public.profiles p
    WHERE p.id = auth.uid() AND p.role = 'OPERATOR'
  )
);

-- updated_at 자동 갱신 (raw_items와 같은 패턴)
CREATE OR REPLACE FUNCTION public.keyword_rules_set_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS keyword_rules_set_updated_at ON public.keyword_rules;
CREATE TRIGGER keyword_rules_set_updated_at
  BEFORE UPDATE ON public.keyword_rules
  FOR EACH ROW EXECUTE FUNCTION public.keyword_rules_set_updated_at();

-- ------------------------------------------------------------
-- 2. admin_db_stats() — 데이터 관리 화면 지표
--    Supabase 대시보드는 하루 1회 갱신이라 실시간 확인용 (pg_database_size)
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.admin_db_stats()
RETURNS jsonb
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT jsonb_build_object(
    'measured_at', now(),
    'database_bytes', pg_database_size(current_database()),
    'tables', (
      SELECT jsonb_agg(t ORDER BY (t->>'total_bytes')::bigint DESC)
      FROM (
        SELECT jsonb_build_object(
          'name', c.relname,
          'total_bytes', pg_total_relation_size(c.oid),
          'table_bytes', pg_relation_size(c.oid),
          'index_bytes', pg_indexes_size(c.oid),
          'toast_bytes', coalesce(pg_total_relation_size(c.reltoastrelid), 0),
          'rows', c.reltuples::bigint
        ) AS t
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        ORDER BY pg_total_relation_size(c.oid) DESC
        LIMIT 12
      ) s
    ),
    'raw_text_bytes', (SELECT coalesce(sum(pg_column_size(raw_text)), 0) FROM raw_items WHERE raw_text IS NOT NULL),
    'clean_text_bytes', (SELECT coalesce(sum(pg_column_size(clean_text)), 0) FROM raw_items WHERE clean_text IS NOT NULL),
    'raw_response_bytes', (SELECT coalesce(sum(pg_column_size(raw_response)), 0) FROM analyses WHERE raw_response IS NOT NULL),
    'extract_pending', (SELECT count(*) FROM raw_items WHERE extract_status = 'PENDING'),
    'extract_pending_over_3d', (
      SELECT count(*) FROM raw_items
      WHERE extract_status = 'PENDING'
        AND coalesce(published_at, fetched_at) < now() - interval '3 days'
    ),
    'analysis_pending', (SELECT count(*) FROM analysis_jobs WHERE status IN ('PENDING', 'RETRY')),
    'analysis_pending_over_3d', (
      SELECT count(*) FROM analysis_jobs
      WHERE status IN ('PENDING', 'RETRY')
        AND source_published_at < now() - interval '3 days'
    ),
    'exclude_with_body', (
      SELECT count(*) FROM raw_items
      WHERE filter_status = 'EXCLUDE' AND clean_text IS NOT NULL
    ),
    'raw_items_total', (SELECT count(*) FROM raw_items),
    'published_total', (SELECT count(*) FROM published_items),
    'analyses_total', (SELECT count(*) FROM analyses)
  );
$$;

REVOKE ALL ON FUNCTION public.admin_db_stats() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.admin_db_stats() FROM anon;
REVOKE ALL ON FUNCTION public.admin_db_stats() FROM authenticated;
GRANT EXECUTE ON FUNCTION public.admin_db_stats() TO service_role;

-- ------------------------------------------------------------
-- 3. 보존 정책 설정 (cleanup 배치가 읽는다 — 값은 jsonb)
-- ------------------------------------------------------------
INSERT INTO public.app_settings (key, value, description) VALUES
  ('extract_expire_days', '3',
   '본문 추출 대기가 발행 후 며칠을 넘기면 EXPIRED로 정리할지 (0=안 함). 지난 뉴스에 Gemini 호출을 쓰지 않기 위함'),
  ('analysis_expire_days', '3',
   '분석 대기 job이 원문 발행 후 며칠을 넘기면 CANCELLED로 정리할지 (0=안 함)'),
  ('nonrep_body_retention_days', '7',
   '클러스터 대표가 아닌 구성원 본문(clean_text)을 며칠 뒤 비울지 — 2차 병합 창(7일)보다 짧으면 안 됨'),
  ('keep_exclude_body', 'false',
   '로컬 필터 EXCLUDE 판정 항목의 본문을 보존할지 (false=저장·보존 안 함)'),
  ('keep_raw_response', 'false',
   'Gemini 응답 원문(analyses.raw_response)을 보존할지 (false=저장 안 함 — 파싱된 필드만 보존)')
ON CONFLICT (key) DO NOTHING;
