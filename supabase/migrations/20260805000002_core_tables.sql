-- ============================================================
-- 0002: 핵심 테이블 18개
-- 관련 설계: design v0.3 §10 / tasks v0.2 §2.3
-- 고정값은 CHECK 제약으로 관리한다 (tasks §2.2).
-- ============================================================

-- ------------------------------------------------------------
-- 1. profiles — 사용자 프로필과 역할
-- ------------------------------------------------------------
CREATE TABLE public.profiles (
  id          uuid PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
  email       text NOT NULL,
  display_name text,
  role        text NOT NULL DEFAULT 'USER'
              CHECK (role IN ('USER', 'OPERATOR')),
  -- 허용 도메인 + 이메일 인증 완료 후 활성화 (FR-001, 설계 §16)
  is_active   boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 2. sources — 수집원 (FR-002)
-- ------------------------------------------------------------
CREATE TABLE public.sources (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL,
  source_type   text NOT NULL CHECK (source_type IN (
                  '정부·공공기관',
                  '기업 공식 발표',
                  '연구기관·대학',
                  '전문 기술 큐레이션 매체',
                  '로봇·산업 전문매체',
                  '일반 언론',
                  '정책·사업 공고'
                )),
  country_region text NOT NULL DEFAULT '국내',
  language      text NOT NULL DEFAULT 'ko',
  url           text NOT NULL,
  fetch_method  text NOT NULL DEFAULT 'RSS'
                CHECK (fetch_method IN ('RSS', 'LIST_PAGE', 'PREDEFINED')),
  -- 사전 정의 어댑터 이름 또는 목록 페이지 추출 규칙 (tasks §5.2)
  adapter_config jsonb,
  fetch_interval_minutes integer NOT NULL DEFAULT 180,
  -- 작업 큐 우선순위 기본값 (설계 §9.2)
  priority      integer NOT NULL DEFAULT 40,
  is_active     boolean NOT NULL DEFAULT true,
  last_success_at timestamptz,
  last_failure_at timestamptz,
  last_error_message text,
  notes         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 3. source_runs — 수집원별 실행 기록 (tasks §5.5)
-- ------------------------------------------------------------
CREATE TABLE public.source_runs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id     uuid NOT NULL REFERENCES public.sources (id) ON DELETE CASCADE,
  workflow_run_id text,
  started_at    timestamptz NOT NULL DEFAULT now(),
  completed_at  timestamptz,
  status        text NOT NULL DEFAULT 'RUNNING'
                CHECK (status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'FAILURE')),
  fetched_count integer NOT NULL DEFAULT 0,
  new_count     integer NOT NULL DEFAULT 0,
  error_message text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 4. manual_submissions — 운영자 수동 URL 등록 (설계 §10.4)
-- ------------------------------------------------------------
CREATE TABLE public.manual_submissions (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  submitted_by  uuid NOT NULL REFERENCES public.profiles (id),
  url           text NOT NULL,
  priority      integer NOT NULL DEFAULT 20,
  status        text NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING', 'PROCESSING', 'DONE', 'DUPLICATE', 'FAILED')),
  raw_item_id   uuid,
  error_message text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  completed_at  timestamptz
);

-- ------------------------------------------------------------
-- 5. raw_items — 수집 원문 (FR-003, FR-005)
-- ------------------------------------------------------------
CREATE TABLE public.raw_items (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id     uuid REFERENCES public.sources (id) ON DELETE SET NULL,
  manual_submission_id uuid REFERENCES public.manual_submissions (id) ON DELETE SET NULL,
  url           text NOT NULL,
  -- URL 정규화 결과 (설계 §8.2). UNIQUE 제약으로 동일 URL 중복 저장 방지.
  canonical_url text NOT NULL,
  title         text,
  author        text,
  feed_summary  text,
  published_at  timestamptz,
  fetched_at    timestamptz NOT NULL DEFAULT now(),
  -- 원문 본문: RAW_TEXT_RETENTION_DAYS 이후 cleanup에서 삭제
  raw_text      text,
  clean_text    text,
  language      text,
  content_hash  text,
  extract_status text NOT NULL DEFAULT 'PENDING'
                CHECK (extract_status IN ('PENDING', 'OK', 'FAILED', 'META_ONLY')),
  -- 로컬 1차 필터 결과 (tasks §6.1)
  filter_status text NOT NULL DEFAULT 'PENDING'
                CHECK (filter_status IN ('PENDING', 'PASS', 'LOW_PRIORITY', 'EXCLUDE')),
  filter_reason text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT raw_items_canonical_url_key UNIQUE (canonical_url)
);

ALTER TABLE public.manual_submissions
  ADD CONSTRAINT manual_submissions_raw_item_fk
  FOREIGN KEY (raw_item_id) REFERENCES public.raw_items (id) ON DELETE SET NULL;

-- ------------------------------------------------------------
-- 6. content_clusters — 중복 클러스터 (FR-005)
-- ------------------------------------------------------------
CREATE TABLE public.content_clusters (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  representative_raw_item_id uuid REFERENCES public.raw_items (id) ON DELETE SET NULL,
  title         text,
  event_date    date,
  -- 기관·기업·정책명 후보 (클러스터 판정 근거, 설계 §8.4)
  entity_keys   text[] NOT NULL DEFAULT '{}',
  member_count  integer NOT NULL DEFAULT 1,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 7. cluster_members — 클러스터 구성원
-- ------------------------------------------------------------
CREATE TABLE public.cluster_members (
  cluster_id    uuid NOT NULL REFERENCES public.content_clusters (id) ON DELETE CASCADE,
  raw_item_id   uuid NOT NULL REFERENCES public.raw_items (id) ON DELETE CASCADE,
  is_representative boolean NOT NULL DEFAULT false,
  similarity_score real,
  added_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (cluster_id, raw_item_id),
  -- 하나의 원문은 하나의 클러스터에만 속한다
  CONSTRAINT cluster_members_raw_item_unique UNIQUE (raw_item_id)
);

-- ------------------------------------------------------------
-- 8. analysis_jobs — AI 분석 작업 큐 (설계 §9)
-- ------------------------------------------------------------
CREATE TABLE public.analysis_jobs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cluster_id    uuid REFERENCES public.content_clusters (id) ON DELETE CASCADE,
  job_type      text NOT NULL DEFAULT 'ARTICLE'
                CHECK (job_type IN ('ARTICLE', 'BRIEF')),
  status        text NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING', 'PROCESSING', 'RETRY', 'DEFERRED',
                                  'DONE', 'FAILED', 'CANCELLED')),
  -- 0 브리프 / 10 정부 / 20 기업·연구 / 30 전문매체 / 40 일반 (설계 §9.2)
  priority      integer NOT NULL DEFAULT 40,
  attempt_count integer NOT NULL DEFAULT 0,
  available_at  timestamptz NOT NULL DEFAULT now(),
  locked_at     timestamptz,
  locked_by     text,
  last_error_code text,
  last_error_message text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  -- 동일 대상·동일 작업 중복 생성 방지 (tasks §7.1)
  CONSTRAINT analysis_jobs_cluster_type_unique UNIQUE (cluster_id, job_type)
);

-- ------------------------------------------------------------
-- 9. analyses — AI 분석 결과 (설계 §10.1)
-- ------------------------------------------------------------
CREATE TABLE public.analyses (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cluster_id      uuid NOT NULL REFERENCES public.content_clusters (id) ON DELETE CASCADE,
  model_name      text NOT NULL,
  prompt_version  text NOT NULL,
  schema_version  text NOT NULL,
  is_robot_related boolean NOT NULL,
  category        text CHECK (category IN ('정책', '산업', '기술')),
  region          text CHECK (region IN ('국내', '미국', '중국', '일본', '유럽', '기타')),
  robot_field     text CHECK (robot_field IN (
                    '휴머노이드·피지컬 AI',
                    '제조·산업용 로봇',
                    '서비스·물류 로봇',
                    '의료·돌봄 로봇',
                    '농업 로봇',
                    '국방·재난 로봇',
                    '해양·특수환경 로봇',
                    '핵심 부품·소프트웨어',
                    '기타'
                  )),
  importance      text CHECK (importance IN ('높음', '보통', '낮음')),
  evidence_level  text CHECK (evidence_level IN ('강함', '보통', '약함')),
  kiro_relevance  text CHECK (kiro_relevance IN ('직접', '간접', '낮음')),
  display_title   text,
  one_line_summary text,
  verified_facts  jsonb NOT NULL DEFAULT '[]'::jsonb,
  numbers_and_dates jsonb NOT NULL DEFAULT '[]'::jsonb,
  ai_interpretation text,
  kiro_implication text,
  limitations     text,
  keywords        jsonb NOT NULL DEFAULT '[]'::jsonb,
  policy_meta     jsonb,
  raw_response    jsonb,
  input_token_count  integer,
  output_token_count integer,
  generated_at    timestamptz NOT NULL DEFAULT now(),
  validation_status text NOT NULL DEFAULT 'PENDING'
                  CHECK (validation_status IN ('PENDING', 'PASS', 'WARN', 'FAIL'))
);

-- ------------------------------------------------------------
-- 10. published_items — 게시 콘텐츠 (설계 §10.3)
-- ------------------------------------------------------------
CREATE TABLE public.published_items (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cluster_id    uuid NOT NULL UNIQUE REFERENCES public.content_clusters (id) ON DELETE CASCADE,
  current_analysis_id uuid NOT NULL REFERENCES public.analyses (id),
  title         text NOT NULL,
  category      text NOT NULL CHECK (category IN ('정책', '산업', '기술')),
  region        text NOT NULL CHECK (region IN ('국내', '미국', '중국', '일본', '유럽', '기타')),
  robot_field   text NOT NULL CHECK (robot_field IN (
                  '휴머노이드·피지컬 AI',
                  '제조·산업용 로봇',
                  '서비스·물류 로봇',
                  '의료·돌봄 로봇',
                  '농업 로봇',
                  '국방·재난 로봇',
                  '해양·특수환경 로봇',
                  '핵심 부품·소프트웨어',
                  '기타'
                )),
  importance    text NOT NULL CHECK (importance IN ('높음', '보통', '낮음')),
  evidence_level text NOT NULL CHECK (evidence_level IN ('강함', '보통', '약함')),
  kiro_relevance text NOT NULL CHECK (kiro_relevance IN ('직접', '간접', '낮음')),
  published_at  timestamptz NOT NULL DEFAULT now(),
  source_published_at timestamptz,
  representative_source_name text,
  representative_url text NOT NULL,
  related_source_count integer NOT NULL DEFAULT 1,
  -- 한국어 부분검색 대상 (설계 §11)
  search_text   text NOT NULL DEFAULT '',
  is_visible    boolean NOT NULL DEFAULT true,
  hidden_reason text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 11. policy_details — 정책·R&D 정규화 (설계 §10.2, FR-010)
-- ------------------------------------------------------------
CREATE TABLE public.policy_details (
  published_item_id uuid PRIMARY KEY REFERENCES public.published_items (id) ON DELETE CASCADE,
  policy_name   text,
  project_name  text,
  ministries    text[],
  organizations text[],
  budget_text   text,
  -- 금액 환산 실패 시 null 유지, budget_text만 보존 (tasks §9.4)
  budget_amount_krw numeric,
  project_start_date date,
  project_end_date   date,
  support_targets text[],
  announcement_status text,
  application_deadline date,
  target_region text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 12. brief_periods — 격주 브리프 기간 (FR-012)
-- ------------------------------------------------------------
CREATE TABLE public.brief_periods (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  period_start  date NOT NULL,
  period_end    date NOT NULL,
  status        text NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING', 'GENERATING', 'PUBLISHED', 'FAILED')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  -- 같은 기간 중복 생성 방지 (tasks §13.1)
  CONSTRAINT brief_periods_range_unique UNIQUE (period_start, period_end)
);

-- ------------------------------------------------------------
-- 13. briefs — 브리프 버전·스냅샷 (tasks §13.5)
-- ------------------------------------------------------------
CREATE TABLE public.briefs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  brief_period_id uuid NOT NULL REFERENCES public.brief_periods (id) ON DELETE CASCADE,
  version       integer NOT NULL DEFAULT 1,
  title         text NOT NULL,
  status        text NOT NULL DEFAULT 'DRAFT'
                CHECK (status IN ('DRAFT', 'PUBLISHED', 'FAILED')),
  -- 3개 섹션 호출 결과 (설계 §14.2)
  section_summary_policy jsonb,
  section_industry_tech  jsonb,
  section_outlook        jsonb,
  -- 코드 조립 최종본 (마크다운). 발행 당시 상태로 보존되는 스냅샷.
  assembled_markdown text,
  model_name    text,
  prompt_version text,
  generated_at  timestamptz,
  published_at  timestamptz,
  is_current    boolean NOT NULL DEFAULT true,
  failure_reason text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT briefs_period_version_unique UNIQUE (brief_period_id, version)
);

-- ------------------------------------------------------------
-- 14. brief_items — 브리프에 사용된 동향 목록
-- ------------------------------------------------------------
CREATE TABLE public.brief_items (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  brief_id      uuid NOT NULL REFERENCES public.briefs (id) ON DELETE CASCADE,
  published_item_id uuid NOT NULL REFERENCES public.published_items (id),
  section       text NOT NULL,
  display_order integer NOT NULL DEFAULT 0,
  is_low_confidence boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT brief_items_unique UNIQUE (brief_id, published_item_id, section)
);

-- ------------------------------------------------------------
-- 15. error_reports — 오류 신고 (FR-014)
-- ------------------------------------------------------------
CREATE TABLE public.error_reports (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  published_item_id uuid NOT NULL REFERENCES public.published_items (id) ON DELETE CASCADE,
  reported_by   uuid NOT NULL REFERENCES public.profiles (id),
  report_type   text NOT NULL CHECK (report_type IN (
                  '사실 오류',
                  '잘못된 출처',
                  '중복',
                  '잘못된 분류',
                  '과도한 해석',
                  '부적절한 KIRO 시사점',
                  '기타'
                )),
  description   text,
  status        text NOT NULL DEFAULT 'OPEN'
                CHECK (status IN ('OPEN', 'RESOLVED', 'DISMISSED')),
  operator_note text,
  resolved_by   uuid REFERENCES public.profiles (id),
  resolved_at   timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 16. app_settings — 런타임 설정 (tasks §1.3)
-- ------------------------------------------------------------
CREATE TABLE public.app_settings (
  key           text PRIMARY KEY,
  value         jsonb NOT NULL,
  description   text,
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 17. operation_events — 운영자 조치 이력 (tasks §14.2)
-- ------------------------------------------------------------
CREATE TABLE public.operation_events (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_type    text NOT NULL CHECK (event_type IN (
                  'CONTENT_HIDE',
                  'CONTENT_UNHIDE',
                  'REANALYZE',
                  'REPORT_RESOLVE',
                  'REPORT_DISMISS',
                  'SOURCE_ENABLE',
                  'SOURCE_DISABLE',
                  'BRIEF_REGENERATE',
                  'MANUAL_SUBMIT',
                  'OTHER'
                )),
  target_table  text NOT NULL,
  target_id     uuid,
  actor_id      uuid REFERENCES public.profiles (id),
  reason        text,
  detail        jsonb,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 18. workflow_usage — 배치 실행·사용량 계측 (설계 §10.5)
-- ------------------------------------------------------------
CREATE TABLE public.workflow_usage (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_name text NOT NULL,
  workflow_run_id text,
  started_at    timestamptz NOT NULL DEFAULT now(),
  completed_at  timestamptz,
  duration_seconds integer,
  fetch_duration_seconds     integer,
  normalize_duration_seconds integer,
  cluster_duration_seconds   integer,
  analysis_duration_seconds  integer,
  collected_count integer NOT NULL DEFAULT 0,
  analyzed_count  integer NOT NULL DEFAULT 0,
  api_call_count  integer NOT NULL DEFAULT 0,
  remaining_pending_count integer,
  oldest_pending_age_minutes integer,
  keepalive_performed boolean NOT NULL DEFAULT false,
  keepalive_status text,
  status        text NOT NULL DEFAULT 'RUNNING'
                CHECK (status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'FAILURE')),
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- gemini_calls — Gemini 호출 기록 (설계 §7.1)
-- 일일 쿼터는 America/Los_Angeles 날짜(quota_date_pt) 기준으로 집계한다.
-- ------------------------------------------------------------
CREATE TABLE public.gemini_calls (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  model_name    text NOT NULL,
  quota_date_pt date NOT NULL,
  called_at_utc timestamptz NOT NULL DEFAULT now(),
  request_count integer NOT NULL DEFAULT 1,
  status        text NOT NULL CHECK (status IN ('OK', 'RATE_LIMITED', 'ERROR')),
  error_code    text,
  job_id        uuid REFERENCES public.analysis_jobs (id) ON DELETE SET NULL
);

-- ------------------------------------------------------------
-- updated_at 트리거 일괄 적용
-- ------------------------------------------------------------
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'profiles', 'sources', 'raw_items', 'content_clusters',
    'analysis_jobs', 'published_items', 'policy_details',
    'brief_periods'
  ]
  LOOP
    EXECUTE format(
      'CREATE TRIGGER %I_set_updated_at
       BEFORE UPDATE ON public.%I
       FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()',
      t, t
    );
  END LOOP;
END;
$$;

-- app_settings는 updated_at 컬럼명이 같아 동일 트리거 사용
CREATE TRIGGER app_settings_set_updated_at
BEFORE UPDATE ON public.app_settings
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
