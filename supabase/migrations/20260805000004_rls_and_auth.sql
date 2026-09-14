-- ============================================================
-- 0004: RLS 정책과 가입 처리
-- 관련 설계: design v0.3 §16 / tasks v0.2 §2.5, §3.2
--
-- 원칙:
--  - 배치(GitHub Actions)는 service role 키를 사용하므로 RLS를 우회한다.
--  - USER: 게시 콘텐츠·브리프 열람, 오류 신고 생성만 가능.
--  - OPERATOR: 운영 테이블 열람과 운영 조치 가능.
--  - raw_items의 관련 출처 표시는 서버 API(service role)를 통해 제공한다.
-- ============================================================

-- ------------------------------------------------------------
-- RLS 헬퍼 (profiles 생성 이후에 정의)
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.is_active_user()
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
STABLE
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.profiles
    WHERE id = auth.uid()
      AND is_active = true
  );
$$;

CREATE OR REPLACE FUNCTION public.is_operator()
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
STABLE
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.profiles
    WHERE id = auth.uid()
      AND is_active = true
      AND role = 'OPERATOR'
  );
$$;

-- ------------------------------------------------------------
-- 가입 시 프로필 자동 생성 + 허용 도메인 검사 (FR-001, 설계 §16)
-- 허용 도메인은 app_settings.allowed_email_domains(jsonb 배열)로 관리한다.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  email_domain text;
  domain_allowed boolean;
BEGIN
  email_domain := lower(split_part(NEW.email, '@', 2));

  SELECT EXISTS (
    SELECT 1
    FROM public.app_settings s,
         jsonb_array_elements_text(s.value) AS d(domain)
    WHERE s.key = 'allowed_email_domains'
      AND lower(d.domain) = email_domain
  ) INTO domain_allowed;

  INSERT INTO public.profiles (id, email, is_active)
  VALUES (NEW.id, NEW.email, COALESCE(domain_allowed, false));

  RETURN NEW;
END;
$$;

CREATE TRIGGER on_auth_user_created
AFTER INSERT ON auth.users
FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- ------------------------------------------------------------
-- 전 테이블 RLS 활성화
-- ------------------------------------------------------------
ALTER TABLE public.profiles           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sources            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.source_runs        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.manual_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.raw_items          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.content_clusters   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cluster_members    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analysis_jobs      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analyses           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.published_items    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.policy_details     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.brief_periods      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.briefs             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.brief_items        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.error_reports      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app_settings       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operation_events   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.workflow_usage     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.gemini_calls       ENABLE ROW LEVEL SECURITY;

-- ------------------------------------------------------------
-- profiles
-- ------------------------------------------------------------
CREATE POLICY profiles_select_own
ON public.profiles FOR SELECT
USING (id = auth.uid() OR public.is_operator());

-- 역할·활성화 변경은 service role(운영 API)에서만 수행한다.

-- ------------------------------------------------------------
-- 게시 콘텐츠: 활성 사용자 열람 (FR-001)
-- ------------------------------------------------------------
CREATE POLICY published_items_select
ON public.published_items FOR SELECT
USING (
  (public.is_active_user() AND is_visible = true)
  OR public.is_operator()
);

CREATE POLICY analyses_select
ON public.analyses FOR SELECT
USING (public.is_active_user());

CREATE POLICY policy_details_select
ON public.policy_details FOR SELECT
USING (public.is_active_user());

CREATE POLICY content_clusters_select
ON public.content_clusters FOR SELECT
USING (public.is_active_user());

-- ------------------------------------------------------------
-- 브리프: 활성 사용자는 발행본만, 운영자는 전체
-- ------------------------------------------------------------
CREATE POLICY brief_periods_select
ON public.brief_periods FOR SELECT
USING (public.is_active_user());

CREATE POLICY briefs_select
ON public.briefs FOR SELECT
USING (
  (public.is_active_user() AND status = 'PUBLISHED')
  OR public.is_operator()
);

CREATE POLICY brief_items_select
ON public.brief_items FOR SELECT
USING (public.is_active_user());

-- ------------------------------------------------------------
-- 오류 신고: 로그인 사용자 생성, 본인 조회, 운영자 전체 (FR-014)
-- ------------------------------------------------------------
CREATE POLICY error_reports_insert
ON public.error_reports FOR INSERT
WITH CHECK (
  public.is_active_user()
  AND reported_by = auth.uid()
);

CREATE POLICY error_reports_select
ON public.error_reports FOR SELECT
USING (reported_by = auth.uid() OR public.is_operator());

CREATE POLICY error_reports_update_operator
ON public.error_reports FOR UPDATE
USING (public.is_operator())
WITH CHECK (public.is_operator());

-- ------------------------------------------------------------
-- 운영 전용 테이블: OPERATOR 열람 (쓰기는 service role API 경유)
-- ------------------------------------------------------------
CREATE POLICY sources_select_operator
ON public.sources FOR SELECT
USING (public.is_operator());

CREATE POLICY sources_write_operator
ON public.sources FOR ALL
USING (public.is_operator())
WITH CHECK (public.is_operator());

CREATE POLICY source_runs_select_operator
ON public.source_runs FOR SELECT
USING (public.is_operator());

CREATE POLICY manual_submissions_operator
ON public.manual_submissions FOR ALL
USING (public.is_operator())
WITH CHECK (public.is_operator());

CREATE POLICY raw_items_select_operator
ON public.raw_items FOR SELECT
USING (public.is_operator());

CREATE POLICY cluster_members_select_operator
ON public.cluster_members FOR SELECT
USING (public.is_operator());

CREATE POLICY analysis_jobs_select_operator
ON public.analysis_jobs FOR SELECT
USING (public.is_operator());

CREATE POLICY app_settings_select_operator
ON public.app_settings FOR SELECT
USING (public.is_operator());

CREATE POLICY operation_events_operator
ON public.operation_events FOR ALL
USING (public.is_operator())
WITH CHECK (public.is_operator());

CREATE POLICY workflow_usage_select_operator
ON public.workflow_usage FOR SELECT
USING (public.is_operator());

CREATE POLICY gemini_calls_select_operator
ON public.gemini_calls FOR SELECT
USING (public.is_operator());
