-- 0015: R&D 사업공고 리스트 (사용자 요청 2026-08-07 — "NTIS R&D 리스트에서 로봇 관련만")
--
-- NTIS 국가R&D통합공고(접수 중)를 수집해 로봇 관련 키워드
-- (로봇·로보틱스·자동화·피지컬AI·스마트공장·AMR 등)로 표식한다.
-- 뉴스 파이프라인(클러스터·Gemini 분석)과 무관한 경량 구조 — AI 호출 0회.
-- board 컬럼: 'NTIS' 확장 여지로 남겨둠 (현재 '국가' 고정).

CREATE TABLE public.rnd_announcements (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  board         text NOT NULL CHECK (board IN ('국가', '부산')),
  agency        text,
  title         text NOT NULL,
  -- 원천기관 공고 페이지 직링크. UNIQUE로 재수집 시 중복 방지(upsert 기준).
  source_url    text NOT NULL,
  posted_date   date,
  -- 수집 시점 D-day에서 환산한 마감일. 상시·표기 없음은 NULL.
  deadline_date date,
  d_day_label   text,
  is_robot_related boolean NOT NULL DEFAULT false,
  matched_keywords text[] NOT NULL DEFAULT '{}',
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT rnd_announcements_url_key UNIQUE (source_url)
);

CREATE INDEX rnd_announcements_robot_deadline_idx
  ON public.rnd_announcements (deadline_date ASC NULLS LAST)
  WHERE is_robot_related;

ALTER TABLE public.rnd_announcements ENABLE ROW LEVEL SECURITY;

-- 공개 공고 데이터 — 익명 열람 허용 (쓰기는 service role만: 정책 없음 = 차단)
CREATE POLICY rnd_announcements_select
ON public.rnd_announcements FOR SELECT
USING (true);
