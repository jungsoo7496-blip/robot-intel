-- ============================================================
-- 초기 설정값 시드
-- 관련 설계: design v0.3 §23 / tasks v0.2 §1.3
-- 실제 값은 배포 전에 확정한다.
-- ============================================================

INSERT INTO public.app_settings (key, value, description) VALUES
  ('allowed_email_domains',
   '["kiro.re.kr"]',
   '가입 허용 이메일 도메인 목록. 배포 전 실제 KIRO 도메인 확정 필요.'),

  ('article_model',
   '"gemini-flash-lite-latest"',
   '기사 분석 모델 (ARTICLE_MODEL)'),

  ('brief_model',
   '"gemini-flash-latest"',
   '브리프 종합 모델 (BRIEF_MODEL)'),

  ('brief_fallback_model',
   '"gemini-flash-lite-latest"',
   '브리프 폴백 모델 (BRIEF_FALLBACK_MODEL)'),

  ('article_request_interval_seconds', '5',  '기사 분석 호출 간격(초)'),
  ('article_batch_max_count',          '40', '배치당 최대 분석 건수'),
  ('article_batch_max_seconds',        '270','배치 내부 종료 시간(4분 30초)'),
  ('article_daily_soft_limit',         '330','Flash Lite 일일 내부 목표(PT 날짜 기준)'),
  ('brief_daily_reserve',              '5',  '브리프 발행일 예약 호출 수'),

  ('raw_text_retention_days',  '30',    '원문 raw_text 보존기간(일)'),
  ('clean_text_max_length',    '12000', 'AI 입력 정제 본문 최대 길이(문자)'),
  ('title_similarity_threshold','85',   '제목 유사도 임계값(0~100, rapidfuzz)'),

  ('backup_retention_days', '28', '백업 artifact 보존기간(일)'),

  ('brief_publish_weekday', '"MON"', '격주 브리프 발행 요일'),
  ('brief_publish_hour_kst', '9',    '격주 브리프 발행 시각(KST)'),

  ('actions_monthly_budget_minutes', '1500', 'GitHub Actions 월간 내부 예산(분)'),
  ('actions_warning_minutes',        '1200', 'Actions 경고 기준(분)')
ON CONFLICT (key) DO NOTHING;
