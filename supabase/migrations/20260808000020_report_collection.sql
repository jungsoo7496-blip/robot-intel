-- 0020: 정책·동향 보고서 수집 — 4층 데이터모델 (보고서 스펙 §5·7)
--
-- Source(포털) → Channel(자료유형/쿼리) → Occurrence(발견 레코드)
--   → Document(canonical 보고서)
-- 소스가 20~30개로 늘어도 이 구조는 불변 — 어댑터·채널만 추가한다.
-- 파일(PDF/HWP)은 절대 저장하지 않는다: 메타데이터·URL·접근상태·분석만.

-- ------------------------------------------------------------
-- 1. report_sources — 사이트/포털
-- ------------------------------------------------------------
CREATE TABLE public.report_sources (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_key    text UNIQUE NOT NULL,      -- 'point' | 'prism' | 'nkis' ...
  name          text NOT NULL,
  base_url      text,
  source_kind   text,                      -- PORTAL | INSTITUTE | LIBRARY ...
  status        text NOT NULL DEFAULT 'CANDIDATE'
                CHECK (status IN ('CANDIDATE','NEEDS_ADAPTER','READY',
                                  'ACTIVE','PAUSED','BROKEN','RETIRED')),
  priority      integer NOT NULL DEFAULT 100,
  owner_org     text,
  description   text,
  notes         text,
  adapter_key   text,                      -- reports/registry.py 의 어댑터 키
  last_collected_at timestamptz,
  last_success_at   timestamptz,
  consecutive_failures integer NOT NULL DEFAULT 0,
  last_error    text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 2. report_source_channels — 소스 내 자료유형/API/쿼리
-- ------------------------------------------------------------
CREATE TABLE public.report_source_channels (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id     uuid NOT NULL REFERENCES public.report_sources (id) ON DELETE CASCADE,
  channel_key   text NOT NULL,
  name          text NOT NULL,
  adapter_key   text,                      -- 없으면 source의 adapter_key 사용
  collection_method text NOT NULL DEFAULT 'API'
                CHECK (collection_method IN ('API','HTML','RSS','CSV','OTHER')),
  source_subtype text,
  enabled       boolean NOT NULL DEFAULT true,
  query         text,
  config_json   jsonb,
  fetch_interval_hours integer NOT NULL DEFAULT 24,
  max_pages_per_run    integer NOT NULL DEFAULT 3,
  max_items_per_run    integer NOT NULL DEFAULT 200,
  request_interval_ms  integer NOT NULL DEFAULT 1500,
  credential_key_name  text,               -- 예: 'NKIS_API_KEY' (secret 자체는 저장 금지)
  supports_abstract        boolean NOT NULL DEFAULT false,
  supports_file_metadata   boolean NOT NULL DEFAULT false,
  supports_direct_download boolean NOT NULL DEFAULT false,
  supports_viewer          boolean NOT NULL DEFAULT false,
  last_run_at     timestamptz,
  last_success_at timestamptz,
  last_error      text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_id, channel_key)
);

-- ------------------------------------------------------------
-- 3. report_source_runs — 실행 통계 (Admin 대시보드용)
-- ------------------------------------------------------------
CREATE TABLE public.report_source_runs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id     uuid REFERENCES public.report_sources (id) ON DELETE CASCADE,
  channel_id    uuid REFERENCES public.report_source_channels (id) ON DELETE SET NULL,
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  status        text,
  fetched_count            integer NOT NULL DEFAULT 0,
  new_occurrence_count     integer NOT NULL DEFAULT 0,
  new_document_count       integer NOT NULL DEFAULT 0,
  duplicate_count          integer NOT NULL DEFAULT 0,
  prefilter_pass_count     integer NOT NULL DEFAULT 0,
  prefilter_excluded_count integer NOT NULL DEFAULT 0,
  access_checked_count     integer NOT NULL DEFAULT 0,
  usable_count             integer NOT NULL DEFAULT 0,
  ai_queued_count          integer NOT NULL DEFAULT 0,
  ai_analyzed_count        integer NOT NULL DEFAULT 0,
  ai_failed_count          integer NOT NULL DEFAULT 0,
  error_count   integer NOT NULL DEFAULT 0,
  notes         text
);

-- ------------------------------------------------------------
-- 4. report_occurrences — 소스에서 발견한 원본 레코드
-- ------------------------------------------------------------
CREATE TABLE public.report_occurrences (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id     uuid NOT NULL REFERENCES public.report_sources (id) ON DELETE CASCADE,
  channel_id    uuid REFERENCES public.report_source_channels (id) ON DELETE SET NULL,
  external_id   text NOT NULL,             -- 소스 내 고유 ID (예: POINT rec_key)
  source_subtype text,
  title         text NOT NULL,
  institution   text,
  authors       text[],
  published_date date,
  published_year integer,
  source_report_type text,
  source_abstract    text,
  source_keywords    jsonb,
  detail_url    text NOT NULL,
  candidate_download_url text,
  access_status text NOT NULL DEFAULT 'UNKNOWN'
                CHECK (access_status IN ('DIRECT_DOWNLOAD','SOURCE_DOWNLOAD',
                                         'VIEW_ONLY','METADATA_ONLY',
                                         'BLOCKED','UNKNOWN')),
  access_checked_at timestamptz,
  access_http_status integer,
  access_note   text,
  file_format   text,
  file_name     text,
  metadata_hash text,
  document_id   uuid,                      -- canonical Document (아래 FK 뒤에 연결)
  first_discovered_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_id, external_id)
);

-- ------------------------------------------------------------
-- 5. report_documents — canonical 보고서 (Gemini 분석 대상)
-- ------------------------------------------------------------
CREATE TABLE public.report_documents (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_title text NOT NULL,
  institution   text,
  authors       text[],
  published_date date,
  published_year integer,
  best_abstract text,
  preferred_occurrence_id uuid REFERENCES public.report_occurrences (id)
                          ON DELETE SET NULL,
  document_fingerprint text UNIQUE,        -- 정규화 제목+기관+연도 해시
  current_analysis_id uuid,
  analysis_status text NOT NULL DEFAULT 'NONE'
                  CHECK (analysis_status IN ('NONE','QUEUED','DONE','FAILED')),
  is_visible    boolean NOT NULL DEFAULT false,
  manual_override boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.report_occurrences
  ADD CONSTRAINT report_occurrences_document_fk
  FOREIGN KEY (document_id) REFERENCES public.report_documents (id)
  ON DELETE SET NULL;

-- ------------------------------------------------------------
-- 6. report_analyses — 구조화 AI 분석 (raw 응답 전문 저장 금지)
-- ------------------------------------------------------------
CREATE TABLE public.report_analyses (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  report_document_id uuid NOT NULL REFERENCES public.report_documents (id)
                     ON DELETE CASCADE,
  analysis_version text,
  prompt_version   text,
  context_version  text,
  requested_model  text,
  served_model     text,
  robot_relevance  text CHECK (robot_relevance IN ('DIRECT','RELATED','WEAK','EXCLUDE')),
  relevance_reason text,
  report_type      text,                   -- 정책·전략/산업·시장/기술·R&D/... (스펙 §21)
  primary_robot_field text,
  robot_fields     jsonb,
  summary          text,
  kiro_relevance   text,
  kiro_relevance_axes jsonb,
  kiro_reason      text,
  keywords         jsonb,
  limitations      text,
  input_hash       text,
  created_at       timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.report_documents
  ADD CONSTRAINT report_documents_current_analysis_fk
  FOREIGN KEY (current_analysis_id) REFERENCES public.report_analyses (id)
  ON DELETE SET NULL;

-- ------------------------------------------------------------
-- 7. report_analysis_jobs — Gemini 배치 큐
-- ------------------------------------------------------------
CREATE TABLE public.report_analysis_jobs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  report_document_id uuid NOT NULL REFERENCES public.report_documents (id)
                     ON DELETE CASCADE,
  status        text NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING','PROCESSING','RETRY',
                                  'DEFERRED','DONE','FAILED')),
  priority      integer NOT NULL DEFAULT 100,
  attempt_count integer NOT NULL DEFAULT 0,
  available_at  timestamptz NOT NULL DEFAULT now(),
  locked_at     timestamptz,
  locked_by     text,
  input_hash    text,
  last_error_code    text,
  last_error_message text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (report_document_id)
);

CREATE INDEX report_occurrences_document_idx ON public.report_occurrences (document_id);
CREATE INDEX report_documents_visible_idx
  ON public.report_documents (published_date DESC) WHERE is_visible;
CREATE INDEX report_analysis_jobs_claim_idx
  ON public.report_analysis_jobs (priority ASC, created_at ASC)
  WHERE status IN ('PENDING','RETRY');

-- ------------------------------------------------------------
-- RLS (스펙 §42): 익명은 공개 canonical 보고서와 그 분석·occurrence만
-- ------------------------------------------------------------
ALTER TABLE public.report_sources         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.report_source_channels ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.report_source_runs     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.report_occurrences     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.report_documents       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.report_analyses        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.report_analysis_jobs   ENABLE ROW LEVEL SECURITY;

CREATE POLICY report_documents_select
ON public.report_documents FOR SELECT
USING (is_visible = true);

CREATE POLICY report_analyses_select
ON public.report_analyses FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.report_documents d
    WHERE d.current_analysis_id = report_analyses.id AND d.is_visible = true
  )
);

-- 공개 문서의 occurrence는 링크·접근상태 표시용으로 익명 열람 허용
CREATE POLICY report_occurrences_select
ON public.report_occurrences FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.report_documents d
    WHERE d.id = report_occurrences.document_id AND d.is_visible = true
  )
);
-- sources/channels/runs/jobs: 익명 정책 없음 = 차단 (service role 전용)
