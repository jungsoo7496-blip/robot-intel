-- 0017: 워크플로 실행 메모 (사용자 피드백 2026-08-08 —
-- "어떤 모델이 몇 번 시도할 건지 안 보여")
-- analyze가 시작 시 "모델 · 최대 건수"를 기록해 운영 화면 표에 보여준다.

ALTER TABLE public.workflow_usage
  ADD COLUMN note text;
