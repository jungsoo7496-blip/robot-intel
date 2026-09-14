-- 0019: Gemini 호출 telemetry 확장 (보고서 스펙 §2.1)
--
-- 어떤 목적(기사/브리프/보고서)으로, 어떤 모델을 요청했고 실제로 어떤
-- 모델이 응답했는지, 토큰을 얼마나 썼는지 추적한다.
-- 기존 model_name은 호환성 위해 유지. telemetry 수집 실패가
-- 분석 자체를 실패시키지 않는다 (전부 nullable).

ALTER TABLE public.gemini_calls
  ADD COLUMN purpose text,           -- ARTICLE | WEEKLY_BRIEF | REPORT_ANALYSIS | OTHER
  ADD COLUMN requested_model text,
  ADD COLUMN served_model text,      -- 응답 metadata의 실제 모델 버전
  ADD COLUMN input_tokens integer,
  ADD COLUMN output_tokens integer,
  ADD COLUMN total_tokens integer;
