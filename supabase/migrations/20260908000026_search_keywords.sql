-- 검색 키워드 통합 (2026-09-08, feature/admin-management)
--
-- 배경: 뉴스 수집원 14개 중 9개가 사실상 '검색어 한 줄'이었고(네이버 5·구글 4),
-- 보고서 채널 24개도 '수집원 × 검색어' 조합을 사람 손으로 하나씩 만든 것이었다.
-- 검색어를 한 목록(search_keywords)에 모으고, 뉴스는 대상 2개(네이버·구글),
-- 보고서는 채널 템플릿 8개가 실행 시점에 그 목록을 곱하게 바꾼다.
--
-- 원칙: **전부 추가(additive)** — DELETE 문이 하나도 없다.
--   · sources 14행 모두 그대로 (raw_items.source_id FK 유지)
--   · report_source_channels 24행 모두 그대로 (report_occurrences.channel_id 1,047건 유지)
--   · 통합으로 물러나는 채널 16개는 삭제가 아니라 enabled=false + retired_at 표시
--
-- 적용 순서: 25(keyword_rules RLS) → 26(이 파일). public.is_operator()와
-- public.set_updated_at()가 이미 있다는 전제 (sources·app_settings 정책이 사용 중).
--
-- 되돌리기: 스키마는 additive라 컬럼만 남기면 무해하다. 데이터는 6단계·5단계의
-- 역UPDATE만 하면 종전 동작으로 돌아간다 — 복원용 원래 값을 해당 UPDATE 옆에
-- 주석으로 남겨 두었다.
--
-- 트랜잭션: 다른 25개 마이그레이션과 같이 이 파일은 BEGIN/COMMIT을 쓰지 않는다.
-- 파일을 감싸는 트랜잭션은 적용하는 쪽이 만든다 —
--   · supabase db push : CLI가 파일마다 트랜잭션을 연다(안에서 또 열면 경고가 나고
--                        안쪽 COMMIT이 바깥 트랜잭션을 먼저 끝내 버린다).
--   · psql 직접 적용   : --single-transaction 을 붙인다 (docs/RESTORE.md 참고).
-- 아래 두 곳의 검증(RAISE EXCEPTION)이 되돌리는 범위도 그 트랜잭션이다.

-- ============================================================
-- 1단계 — 스키마
-- ============================================================

-- 1) search_keywords — 뉴스·보고서 공용 검색어 목록
--    식별자는 term 자체다(슬러그 없음). 지웠다 같은 검색어를 다시 넣으면
--    기존 관리행이 되살아나 수집 이력이 이어진다.
CREATE TABLE IF NOT EXISTS public.search_keywords (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  term       text NOT NULL UNIQUE CHECK (btrim(term) <> '' AND length(term) <= 60),
  -- 같은 뜻 다른 표기(영문 등). 구글 질의에서만 OR로 묶어 쓴다.
  alt_terms  text[] NOT NULL DEFAULT '{}',
  -- 쓰는 곳: 뉴스+보고서 / 뉴스만 / 보고서만 (끄고 켜는 토글이 아니다)
  scope      text NOT NULL DEFAULT 'ALL' CHECK (scope IN ('ALL', 'NEWS', 'REPORTS')),
  sort_order integer NOT NULL DEFAULT 100,
  note       text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS search_keywords_scope_idx
  ON public.search_keywords (scope, sort_order);

-- 2) news_search_targets — 뉴스 검색 대상별 설정 1벌
--    app_settings(jsonb 한 덩어리)가 아니라 표로 둔 이유: 화면 폼이 항목별로
--    범위를 검증해야 하고(간격·건수), 배치가 sources 행을 만들 때 컬럼으로 읽는다.
--    행 추가·삭제는 없다 — 대상은 코드에 어댑터가 있는 2개뿐이다.
CREATE TABLE IF NOT EXISTS public.news_search_targets (
  target_key  text PRIMARY KEY CHECK (target_key IN ('naver', 'google')),
  name        text NOT NULL,
  is_active   boolean NOT NULL DEFAULT true,
  priority    integer NOT NULL DEFAULT 40,
  fetch_interval_minutes integer NOT NULL DEFAULT 100
    CHECK (fetch_interval_minutes BETWEEN 10 AND 1440),
  -- 네이버 전용(한 번에 볼 기사 수). 구글 RSS는 이 값을 쓰지 않는다.
  display     integer NOT NULL DEFAULT 300 CHECK (display BETWEEN 100 AND 300),
  source_type text NOT NULL DEFAULT '일반 언론',
  country_region text NOT NULL DEFAULT '국내',
  language    text NOT NULL DEFAULT 'ko',
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- 3) sources 확장 — '검색 키워드 화면이 소유하는 자동 생성 행' 표시
--    fetch_method는 지금과 같이 NAVER_API / RSS를 그대로 쓴다(collect.py 디스패치 무변경).
--    뉴스 수집원 화면은 managed_by IS NULL만 보여 준다.
ALTER TABLE public.sources ADD COLUMN IF NOT EXISTS managed_by text;
ALTER TABLE public.sources ADD COLUMN IF NOT EXISTS search_target text;
ALTER TABLE public.sources ADD COLUMN IF NOT EXISTS keyword_term text;

ALTER TABLE public.sources DROP CONSTRAINT IF EXISTS sources_managed_by_check;
ALTER TABLE public.sources
  ADD CONSTRAINT sources_managed_by_check
  CHECK (managed_by IS NULL OR managed_by IN ('keyword_search'));

ALTER TABLE public.sources DROP CONSTRAINT IF EXISTS sources_search_target_check;
ALTER TABLE public.sources
  ADD CONSTRAINT sources_search_target_check
  CHECK (search_target IS NULL OR search_target IN ('naver', 'google'));

ALTER TABLE public.sources DROP CONSTRAINT IF EXISTS sources_managed_shape;
ALTER TABLE public.sources
  ADD CONSTRAINT sources_managed_shape
  CHECK (managed_by IS NULL OR (search_target IS NOT NULL AND keyword_term IS NOT NULL));

-- 배치의 UPSERT가 추론하는 인덱스. ON CONFLICT 절에도 같은 WHERE가 있어야 한다.
CREATE UNIQUE INDEX IF NOT EXISTS sources_keyword_search_uniq
  ON public.sources (search_target, keyword_term)
  WHERE managed_by = 'keyword_search';

-- 4) report_source_channels 확장 — 채널을 '수집원 하위 갈래 템플릿'으로
--    채널 = (수집원 × 하위 구분). nanet의 dbname(웹자료/세미나자료),
--    point의 category(chamgo/digital)는 채널이 2개로 남아 그대로 보존된다.
ALTER TABLE public.report_source_channels
  ADD COLUMN IF NOT EXISTS uses_keywords boolean NOT NULL DEFAULT true;
ALTER TABLE public.report_source_channels
  ADD COLUMN IF NOT EXISTS retired_at timestamptz;

-- 실행 기록에 어떤 검색어였는지 남긴다 (운영 화면 추적용)
ALTER TABLE public.report_source_runs
  ADD COLUMN IF NOT EXISTS keyword_term text;

-- 5) report_channel_keyword_runs — (채널 × 검색어) 회전 상태
--    운영자가 만지는 표가 아니다. 채널당 last_run_at 하나로는 검색어 7개를
--    관리할 수 없어서 둔다. 검색어를 지웠다 다시 넣어도 term 기준이라 이어진다.
CREATE TABLE IF NOT EXISTS public.report_channel_keyword_runs (
  channel_id   uuid NOT NULL
    REFERENCES public.report_source_channels(id) ON DELETE CASCADE,
  keyword_term text NOT NULL DEFAULT '',   -- '' = 검색어 미사용 채널(prism)
  last_run_at     timestamptz,
  last_success_at timestamptz,
  last_error      text,
  PRIMARY KEY (channel_id, keyword_term)
);

CREATE INDEX IF NOT EXISTS report_channel_keyword_runs_due_idx
  ON public.report_channel_keyword_runs (last_run_at NULLS FIRST);

-- 6) RLS — 화면은 service role, 배치는 DB URL로 접근하므로 authenticated 경로만 통제
ALTER TABLE public.search_keywords ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.news_search_targets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.report_channel_keyword_runs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS search_keywords_operator ON public.search_keywords;
CREATE POLICY search_keywords_operator
ON public.search_keywords FOR ALL
TO authenticated
USING (public.is_operator())
WITH CHECK (public.is_operator());

DROP POLICY IF EXISTS news_search_targets_operator ON public.news_search_targets;
CREATE POLICY news_search_targets_operator
ON public.news_search_targets FOR ALL
TO authenticated
USING (public.is_operator())
WITH CHECK (public.is_operator());

-- report_channel_keyword_runs: 정책 없음 = service role 전용
-- (report_source_runs·report_source_channels와 같은 취급)

-- updated_at 자동 갱신 (keyword_rules·sources와 같은 패턴 — public.set_updated_at)
DROP TRIGGER IF EXISTS search_keywords_set_updated_at ON public.search_keywords;
CREATE TRIGGER search_keywords_set_updated_at
  BEFORE UPDATE ON public.search_keywords
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS news_search_targets_set_updated_at ON public.news_search_targets;
CREATE TRIGGER news_search_targets_set_updated_at
  BEFORE UPDATE ON public.news_search_targets
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ============================================================
-- 2단계 — 검색어 시드 (현재 상태를 그대로 옮긴 것. 새 검색어는 없다)
--   · '피지컬 AI'의 alt_terms가 지금 구글 행의 복합 질의를 흡수한다
--     → 구글 질의 = "피지컬 AI" OR "Physical AI" OR "Embodied AI" (현재 URL과 글자까지 동일)
--   · scope: 네이버에만 있던 '로봇 정책'·'한국로봇융합연구원'은 NEWS,
--     보고서에만 있던 '협동로봇'·'드론'·'자율주행'은 REPORTS
-- ============================================================
INSERT INTO public.search_keywords (term, alt_terms, scope, sort_order) VALUES
  ('로봇',              '{}',                                 'ALL',     10),
  ('휴머노이드',         '{}',                                 'ALL',     20),
  ('로보틱스',           '{}',                                 'ALL',     30),
  ('피지컬 AI',          '{"Physical AI","Embodied AI"}',      'ALL',     40),
  ('협동로봇',           '{}',                                 'REPORTS', 50),
  ('드론',              '{}',                                 'REPORTS', 60),
  ('자율주행',           '{}',                                 'REPORTS', 70),
  ('로봇 정책',          '{}',                                 'NEWS',    80),
  ('한국로봇융합연구원',   '{}',                                 'NEWS',    90)
ON CONFLICT (term) DO NOTHING;

-- ============================================================
-- 3단계 — 뉴스 검색 대상 시드 (2행 고정)
-- ============================================================
INSERT INTO public.news_search_targets (target_key, name) VALUES
  ('naver',  '네이버 뉴스 검색'),
  ('google', '구글 뉴스 RSS')
ON CONFLICT (target_key) DO NOTHING;

-- ============================================================
-- 4단계 — 기존 뉴스 수집원 9행 태깅 (화이트리스트 UPDATE)
--   이 9행은 비활성화하지 않는다. 이름·URL·adapter_config는 다음 배치의
--   sync_news_search_sources가 다시 계산한다.
--   네이버는 url이 유일하고 짧아 url 기준, 구글은 URL이 길어 오매치 위험이
--   있으므로 name 기준으로 잡는다(현재 이름이 각각 유일함을 확인).
-- ============================================================

-- 네이버 5행 (url 기준)
UPDATE public.sources SET managed_by = 'keyword_search', search_target = 'naver',
       keyword_term = '로봇'
 WHERE url = 'naver-news://query/robot';
UPDATE public.sources SET managed_by = 'keyword_search', search_target = 'naver',
       keyword_term = '로봇 정책'
 WHERE url = 'naver-news://query/robot-policy';
UPDATE public.sources SET managed_by = 'keyword_search', search_target = 'naver',
       keyword_term = '피지컬 AI'
 WHERE url = 'naver-news://query/physical-ai';
UPDATE public.sources SET managed_by = 'keyword_search', search_target = 'naver',
       keyword_term = '한국로봇융합연구원'
 WHERE url = 'naver-news://query/kiro';
UPDATE public.sources SET managed_by = 'keyword_search', search_target = 'naver',
       keyword_term = '휴머노이드'
 WHERE url = 'naver-news://query/humanoid';

-- 구글 4행 (name 기준 — 현재 이름이 유일하다)
UPDATE public.sources SET managed_by = 'keyword_search', search_target = 'google',
       keyword_term = '로봇'
 WHERE name = 'Google 뉴스 — 로봇 (국내 언론 종합)';
UPDATE public.sources SET managed_by = 'keyword_search', search_target = 'google',
       keyword_term = '로보틱스'
 WHERE name = 'Google 뉴스 — 로보틱스 (국내 언론 종합)';
UPDATE public.sources SET managed_by = 'keyword_search', search_target = 'google',
       keyword_term = '휴머노이드'
 WHERE name = 'Google 뉴스 — 휴머노이드 (국내 언론 종합)';
UPDATE public.sources SET managed_by = 'keyword_search', search_target = 'google',
       keyword_term = '피지컬 AI'
 WHERE name = 'Google 뉴스 — 피지컬 AI (국내 언론 종합)';

-- 태깅 검증 — 하나라도 어긋나면 이 파일을 감싼 트랜잭션 전체를 되돌린다.
-- 빈 DB(RESTORE.md의 마이그레이션 일괄 적용)에서는 검증할 대상이 없으므로 건너뛴다.
DO $$
DECLARE n int; n_naver int; n_google int;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.sources) THEN
    RAISE NOTICE '검색 키워드: sources가 비어 있어 태깅 검증을 건너뜁니다(빈 DB 초기화)';
    RETURN;
  END IF;
  SELECT count(*) INTO n FROM public.sources WHERE managed_by = 'keyword_search';
  SELECT count(*) INTO n_naver FROM public.sources
   WHERE managed_by = 'keyword_search' AND search_target = 'naver';
  SELECT count(*) INTO n_google FROM public.sources
   WHERE managed_by = 'keyword_search' AND search_target = 'google';
  IF n <> 9 OR n_naver <> 5 OR n_google <> 4 THEN
    RAISE EXCEPTION '관리행 태깅 실패: 전체 %개(네이버 %, 구글 %) — 9(5·4)여야 합니다',
      n, n_naver, n_google;
  END IF;
END $$;

-- ============================================================
-- 5단계 — 보고서 채널 8개를 '하위 갈래 템플릿'으로 승격
--   공통: query = NULL(실행 시 검색어 주입), retired_at = NULL, enabled = true
--   mp/mi/rim/config_json/credential_key_name은 손대지 않는다 —
--   살아남는 행이 각 그룹에서 값이 가장 큰 '로봇' 행이라 어떤 검색어도
--   종전보다 적게 긁지 않는다(예: nanet 웹자료 mp=10·mi=200 유지).
--   channel_key IN (옛 키, 새 키)로 잡아 재실행해도 안전하게 한다.
-- ============================================================

-- alio/research-robot → research (되돌리기: channel_key='research-robot',
--   name='연구보고서 — 로봇', query='로봇')
UPDATE public.report_source_channels c
   SET channel_key = 'research', name = '연구보고서',
       query = NULL, uses_keywords = true, retired_at = NULL, enabled = true,
       updated_at = now()
  FROM public.report_sources s
 WHERE s.id = c.source_id AND s.source_key = 'alio'
   AND c.channel_key IN ('research-robot', 'research');

-- nanet/web-robot → web (되돌리기: 'web-robot', '웹자료 — 로봇', query='로봇')
UPDATE public.report_source_channels c
   SET channel_key = 'web', name = '웹자료',
       query = NULL, uses_keywords = true, retired_at = NULL, enabled = true,
       updated_at = now()
  FROM public.report_sources s
 WHERE s.id = c.source_id AND s.source_key = 'nanet'
   AND c.channel_key IN ('web-robot', 'web');

-- nanet/seminar-robot → seminar (되돌리기: 'seminar-robot', '세미나자료 — 로봇', query='로봇')
UPDATE public.report_source_channels c
   SET channel_key = 'seminar', name = '세미나자료',
       query = NULL, uses_keywords = true, retired_at = NULL, enabled = true,
       updated_at = now()
  FROM public.report_sources s
 WHERE s.id = c.source_id AND s.source_key = 'nanet'
   AND c.channel_key IN ('seminar-robot', 'seminar');

-- nkis/report-robot → report (되돌리기: 'report-robot', '연구보고서 — 로봇', query='로봇')
UPDATE public.report_source_channels c
   SET channel_key = 'report', name = '연구보고서',
       query = NULL, uses_keywords = true, retired_at = NULL, enabled = true,
       updated_at = now()
  FROM public.report_sources s
 WHERE s.id = c.source_id AND s.source_key = 'nkis'
   AND c.channel_key IN ('report-robot', 'report');

-- point/chamgo-robot → chamgo (되돌리기: 'chamgo-robot', '정책자료 검색 — 로봇', query='로봇')
UPDATE public.report_source_channels c
   SET channel_key = 'chamgo', name = '정책자료(참고자료)',
       query = NULL, uses_keywords = true, retired_at = NULL, enabled = true,
       updated_at = now()
  FROM public.report_sources s
 WHERE s.id = c.source_id AND s.source_key = 'point'
   AND c.channel_key IN ('chamgo-robot', 'chamgo');

-- point/digital-robot → digital (되돌리기: 'digital-robot', '디지털 정책자료 — 로봇', query='로봇')
UPDATE public.report_source_channels c
   SET channel_key = 'digital', name = '디지털 정책자료',
       query = NULL, uses_keywords = true, retired_at = NULL, enabled = true,
       updated_at = now()
  FROM public.report_sources s
 WHERE s.id = c.source_id AND s.source_key = 'point'
   AND c.channel_key IN ('digital-robot', 'digital');

-- scienceon/report-robot → report (되돌리기: 'report-robot', 'R&D 보고서 — 로봇', query='로봇')
UPDATE public.report_source_channels c
   SET channel_key = 'report', name = 'R&D 보고서',
       query = NULL, uses_keywords = true, retired_at = NULL, enabled = true,
       updated_at = now()
  FROM public.report_sources s
 WHERE s.id = c.source_id AND s.source_key = 'scienceon'
   AND c.channel_key IN ('report-robot', 'report');

-- prism/research-window — 키 그대로. 검색어를 쓰지 않는 유일한 채널이다
-- (전체 조회 후 제목 프리필터). 되돌리기: name='정책연구 과제 (당해+전년 구간)'
UPDATE public.report_source_channels c
   SET name = '정책연구 과제 (검색어 없이 전체 조회)',
       query = NULL, uses_keywords = false, retired_at = NULL, enabled = true,
       updated_at = now()
  FROM public.report_sources s
 WHERE s.id = c.source_id AND s.source_key = 'prism'
   AND c.channel_key = 'research-window';

-- ============================================================
-- 6단계 — 나머지 16개 채널 은퇴 (삭제 아님)
--   대상: alio 3(research-autonomous/drone/humanoid), nanet 1(web-humanoid),
--         nkis 3(report-autonomous/drone/humanoid),
--         point 6(chamgo-cobot/drone/humanoid/physicalai/robotics, digital-humanoid),
--         scienceon 3(report-autonomous/drone/humanoid)
--   report_occurrences.channel_id가 계속 이 행들을 가리킨다(의도) — 통계·화면에
--   옛 채널 이름이 남는 것을 '오류'로 오해하지 않도록 이름에 사유를 적어 둔다.
--   되돌리기: 아래 행들의 retired_at=NULL, enabled=true, 이름 접미사 제거,
--             query를 이름 뒤 검색어로 복원(자율주행·드론·휴머노이드·협동로봇·로보틱스·피지컬 AI)
-- ============================================================
UPDATE public.report_source_channels
   SET enabled = false,
       retired_at = now(),
       name = name || ' (검색 키워드로 통합 — 수집 기록 보존용)',
       updated_at = now()
 WHERE retired_at IS NULL
   AND query IS NOT NULL;   -- 5단계에서 템플릿 8개는 query=NULL이 됐다

-- 승격·은퇴 검증
DO $$
DECLARE n_live int; n_retired int; n_kw int; n_nokw int;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.report_source_channels) THEN
    RAISE NOTICE '검색 키워드: 보고서 채널이 비어 있어 통합 검증을 건너뜁니다(빈 DB 초기화)';
    RETURN;
  END IF;
  SELECT count(*) INTO n_live FROM public.report_source_channels
   WHERE retired_at IS NULL;
  SELECT count(*) INTO n_retired FROM public.report_source_channels
   WHERE retired_at IS NOT NULL;
  SELECT count(*) INTO n_kw FROM public.report_source_channels
   WHERE retired_at IS NULL AND uses_keywords;
  SELECT count(*) INTO n_nokw FROM public.report_source_channels
   WHERE retired_at IS NULL AND NOT uses_keywords;
  IF n_live <> 8 OR n_retired <> 16 OR n_kw <> 7 OR n_nokw <> 1 THEN
    RAISE EXCEPTION
      '채널 통합 실패: 살아있는 % / 은퇴 % (검색어 사용 %, 미사용 %) — 8/16(7·1)이어야 합니다',
      n_live, n_retired, n_kw, n_nokw;
  END IF;
  -- 살아있는 채널에 검색어가 남아 있으면 안 된다(실행 시 주입해야 하므로)
  IF EXISTS (SELECT 1 FROM public.report_source_channels
              WHERE retired_at IS NULL AND query IS NOT NULL) THEN
    RAISE EXCEPTION '템플릿 채널에 query가 남아 있습니다 — 5단계 UPDATE를 확인하세요';
  END IF;
END $$;

-- ============================================================
-- 7단계 — report_channel_keyword_runs 시드 없음 (의도)
--   비워 두면 (채널 × 검색어) 50조합이 전부 due가 되고, 새 정렬
--   (last_run_at ASC NULLS FIRST)이 예산 안에서 골고루 돌린다.
--   첫 1~2일에 걸쳐 전 조합이 한 바퀴 돈다.
--   백업에서도 제외한다 — 비어 있으면 스스로 다시 채워지는 회전 상태다
--   (.github/workflows/backup.yml).
-- ============================================================
