-- ============================================================
-- 0013: 브리프 발행주기 격주→주간 전환 (외부 리뷰 제안)
-- 기존 격주 브리프는 삭제하지 않고 BIWEEKLY로 식별해 보관한다.
-- ============================================================

ALTER TABLE public.brief_periods
  ADD COLUMN cadence text NOT NULL DEFAULT 'WEEKLY'
  CHECK (cadence IN ('WEEKLY', 'BIWEEKLY'));

-- 기존 기간은 모두 격주 체제에서 생성됨
UPDATE public.brief_periods SET cadence = 'BIWEEKLY';
