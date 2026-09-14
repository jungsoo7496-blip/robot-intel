-- ============================================================
-- 0012: 로봇 분야 분리(서비스/물류) + 정렬 랭크 (사용자 피드백)
-- - '서비스·물류 로봇'을 '서비스 로봇'/'물류 로봇'으로 분리.
--   기존 데이터는 legacy 값으로 유지하고, 재분석이 진행되며 자연 전환.
-- - 중요도·KIRO 관련도 정렬용 생성 컬럼 (한국어 enum은 DB 정렬 불가)
-- ============================================================

ALTER TABLE public.analyses
  DROP CONSTRAINT analyses_robot_field_check;
ALTER TABLE public.analyses
  ADD CONSTRAINT analyses_robot_field_check CHECK (robot_field IN (
    '휴머노이드·피지컬 AI',
    '제조·산업용 로봇',
    '서비스 로봇',
    '물류 로봇',
    '서비스·물류 로봇',  -- legacy (v1 분석 보존용)
    '의료·돌봄 로봇',
    '농업 로봇',
    '국방·재난 로봇',
    '해양·특수환경 로봇',
    '핵심 부품·소프트웨어',
    '기타'
  ));

ALTER TABLE public.published_items
  DROP CONSTRAINT published_items_robot_field_check;
ALTER TABLE public.published_items
  ADD CONSTRAINT published_items_robot_field_check CHECK (robot_field IN (
    '휴머노이드·피지컬 AI',
    '제조·산업용 로봇',
    '서비스 로봇',
    '물류 로봇',
    '서비스·물류 로봇',  -- legacy
    '의료·돌봄 로봇',
    '농업 로봇',
    '국방·재난 로봇',
    '해양·특수환경 로봇',
    '핵심 부품·소프트웨어',
    '기타'
  ));

-- 정렬 랭크 생성 컬럼 (중요도순 / KIRO 관련도순)
ALTER TABLE public.published_items
  ADD COLUMN importance_rank smallint GENERATED ALWAYS AS (
    CASE importance WHEN '높음' THEN 0 WHEN '보통' THEN 1 ELSE 2 END
  ) STORED,
  ADD COLUMN kiro_relevance_rank smallint GENERATED ALWAYS AS (
    CASE kiro_relevance WHEN '직접' THEN 0 WHEN '간접' THEN 1 ELSE 2 END
  ) STORED;

CREATE INDEX published_items_importance_sort_idx
ON public.published_items (importance_rank, published_at DESC)
WHERE is_visible = true;

CREATE INDEX published_items_kiro_sort_idx
ON public.published_items (kiro_relevance_rank, published_at DESC)
WHERE is_visible = true;
