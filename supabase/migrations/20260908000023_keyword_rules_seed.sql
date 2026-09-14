-- keyword_rules 시드 (2026-09-08, feature/admin-management — 키워드 규칙 관리 화면)
-- 코드·JSON 세 곳에 흩어져 있던 키워드를 DB로 옮긴다. 배치는 시작할 때 이 표를
-- 읽고, DB가 비었거나 못 읽으면 코드에 남아 있는 같은 목록을 기본값으로 쓴다.
--   news_filter  ← scripts/kiro_batch/filter_rules.json   (robot / strong / exclude / event_only)
--   report_tier  ← scripts/kiro_batch/reports/classify.py (core / tool)
--   rnd_keywords ← scripts/kiro_batch/collect_rnd.py      (keyword / pattern)
-- 전부 추가 전용(ON CONFLICT DO NOTHING) — 운영자가 화면에서 고친 값을 덮어쓰지 않는다.
-- 이 파일은 파이썬 상수에서 생성했다 (수작업 옮김 오류 방지).
--
-- 정규식(is_regex=true)은 파이썬 re 문법 그대로 저장한다. 백슬래시가 글자 그대로
-- 들어가야 하므로 표준 문자열 모드를 명시한다 (Supabase·psql 기본값과 같다).
SET standard_conforming_strings = on;

-- ------------------------------------------------------------
-- 1. news_filter — 뉴스 로컬 1차 필터 (local_filter.py)
-- ------------------------------------------------------------

-- robot: 제목·본문에 하나도 없으면 로봇 무관으로 제외
INSERT INTO public.keyword_rules (rule_set, kind, term, is_regex, note) VALUES
  ('news_filter', 'robot', '로봇', false, NULL),
  ('news_filter', 'robot', '로보틱스', false, NULL),
  ('news_filter', 'robot', '휴머노이드', false, NULL),
  ('news_filter', 'robot', '피지컬 AI', false, NULL),
  ('news_filter', 'robot', '물리 AI', false, NULL),
  ('news_filter', 'robot', '협동로봇', false, NULL),
  ('news_filter', 'robot', '산업용 로봇', false, NULL),
  ('news_filter', 'robot', '서비스 로봇', false, NULL),
  ('news_filter', 'robot', '물류 로봇', false, NULL),
  ('news_filter', 'robot', '배송 로봇', false, NULL),
  ('news_filter', 'robot', '의료 로봇', false, NULL),
  ('news_filter', 'robot', '수술 로봇', false, NULL),
  ('news_filter', 'robot', '돌봄 로봇', false, NULL),
  ('news_filter', 'robot', '재활 로봇', false, NULL),
  ('news_filter', 'robot', '농업 로봇', false, NULL),
  ('news_filter', 'robot', '방제 로봇', false, NULL),
  ('news_filter', 'robot', '국방 로봇', false, NULL),
  ('news_filter', 'robot', '재난 로봇', false, NULL),
  ('news_filter', 'robot', '수중 로봇', false, NULL),
  ('news_filter', 'robot', '해양 로봇', false, NULL),
  ('news_filter', 'robot', '드론', false, NULL),
  ('news_filter', 'robot', '무인기', false, NULL),
  ('news_filter', 'robot', '자율주행', false, NULL),
  ('news_filter', 'robot', '무인화', false, NULL),
  ('news_filter', 'robot', '매니퓰레이터', false, NULL),
  ('news_filter', 'robot', '그리퍼', false, NULL),
  ('news_filter', 'robot', '액추에이터', false, NULL),
  ('news_filter', 'robot', '감속기', false, NULL),
  ('news_filter', 'robot', '로봇 팔', false, NULL),
  ('news_filter', 'robot', 'AMR', false, NULL),
  ('news_filter', 'robot', 'AGV', false, NULL),
  ('news_filter', 'robot', 'SLAM', false, NULL),
  ('news_filter', 'robot', '말단장치', false, NULL),
  ('news_filter', 'robot', '엔드이펙터', false, NULL),
  ('news_filter', 'robot', 'robot', false, NULL),
  ('news_filter', 'robot', 'robotics', false, NULL),
  ('news_filter', 'robot', 'humanoid', false, NULL),
  ('news_filter', 'robot', 'physical ai', false, NULL),
  ('news_filter', 'robot', 'manipulator', false, NULL),
  ('news_filter', 'robot', 'gripper', false, NULL),
  ('news_filter', 'robot', 'actuator', false, NULL),
  ('news_filter', 'robot', 'autonomous', false, NULL),
  ('news_filter', 'robot', 'cobot', false, NULL),
  ('news_filter', 'robot', 'exoskeleton', false, NULL),
  ('news_filter', 'robot', '외골격', false, NULL),
  ('news_filter', 'robot', '웨어러블 로봇', false, NULL),
  ('news_filter', 'robot', '보행 로봇', false, NULL),
  ('news_filter', 'robot', '4족', false, NULL),
  ('news_filter', 'robot', '이족보행', false, NULL),
  ('news_filter', 'robot', 'embodied ai', false, NULL),
  ('news_filter', 'robot', 'embodied intelligence', false, NULL),
  ('news_filter', 'robot', '체화 AI', false, NULL),
  ('news_filter', 'robot', '체화지능', false, NULL),
  ('news_filter', 'robot', '체화 지능', false, NULL)
ON CONFLICT (rule_set, kind, term) DO NOTHING;

-- strong: 제목에 있으면 확실한 통과
INSERT INTO public.keyword_rules (rule_set, kind, term, is_regex, note) VALUES
  ('news_filter', 'strong', '로봇', false, NULL),
  ('news_filter', 'strong', '휴머노이드', false, NULL),
  ('news_filter', 'strong', '로보틱스', false, NULL),
  ('news_filter', 'strong', 'robot', false, NULL),
  ('news_filter', 'strong', 'robotics', false, NULL),
  ('news_filter', 'strong', 'humanoid', false, NULL),
  ('news_filter', 'strong', 'cobot', false, NULL)
ON CONFLICT (rule_set, kind, term) DO NOTHING;

-- exclude: 광고·채용·주가 등 즉시 제외 (정규식, 제목+본문)
INSERT INTO public.keyword_rules (rule_set, kind, term, is_regex, note) VALUES
  ('news_filter', 'exclude', '\[채용\]', true, NULL),
  ('news_filter', 'exclude', '채용공고', true, NULL),
  ('news_filter', 'exclude', '신입사원 모집', true, NULL),
  ('news_filter', 'exclude', '경력직 모집', true, NULL),
  ('news_filter', 'exclude', '인재 채용', true, NULL),
  ('news_filter', 'exclude', '\[광고\]', true, NULL),
  ('news_filter', 'exclude', '\[협찬\]', true, NULL),
  ('news_filter', 'exclude', 'AD\b', true, '영문 광고 표기 — ''ad'' 뒤가 단어 끝이면 일치 (load·road 같은 단어도 걸릴 수 있음)'),
  ('news_filter', 'exclude', '스폰서드', true, NULL),
  ('news_filter', 'exclude', '주가 급등', true, NULL),
  ('news_filter', 'exclude', '주가 급락', true, NULL),
  ('news_filter', 'exclude', '상한가', true, NULL),
  ('news_filter', 'exclude', '하한가', true, NULL),
  ('news_filter', 'exclude', '테마주', true, NULL),
  ('news_filter', 'exclude', '관련주 총정리', true, NULL),
  ('news_filter', 'exclude', '이벤트 당첨', true, NULL),
  ('news_filter', 'exclude', '경품', true, NULL),
  ('news_filter', 'exclude', '할인 쿠폰', true, NULL),
  ('news_filter', 'exclude', '특가', true, NULL),
  ('news_filter', 'exclude', '포토뉴스', true, NULL),
  ('news_filter', 'exclude', '화보', true, NULL),
  ('news_filter', 'exclude', '\[부고\]', true, NULL),
  ('news_filter', 'exclude', '\[인사\]', true, NULL)
ON CONFLICT (rule_set, kind, term) DO NOTHING;

-- event_only: 단순 행사 안내 — 제목에 있으면 낮은 우선순위 (정규식)
INSERT INTO public.keyword_rules (rule_set, kind, term, is_regex, note) VALUES
  ('news_filter', 'event_only', '참가자 모집', true, NULL),
  ('news_filter', 'event_only', '사전등록', true, NULL),
  ('news_filter', 'event_only', '관람객 모집', true, NULL),
  ('news_filter', 'event_only', '부스 안내', true, NULL),
  ('news_filter', 'event_only', '전시회 개최', true, NULL)
ON CONFLICT (rule_set, kind, term) DO NOTHING;

-- ------------------------------------------------------------
-- 2. report_tier — 보고서 계층 (reports/classify.py)
-- ------------------------------------------------------------

-- core: 로봇이 핵심 주제 — 정상 우선순위로 분석
INSERT INTO public.keyword_rules (rule_set, kind, term, is_regex, note) VALUES
  ('report_tier', 'core', '로봇', false, NULL),
  ('report_tier', 'core', 'robot', false, NULL),
  ('report_tier', 'core', 'robotics', false, NULL),
  ('report_tier', 'core', '로보틱스', false, NULL),
  ('report_tier', 'core', '휴머노이드', false, NULL),
  ('report_tier', 'core', 'humanoid', false, NULL),
  ('report_tier', 'core', '피지컬 ai', false, NULL),
  ('report_tier', 'core', '피지컬ai', false, NULL),
  ('report_tier', 'core', 'physical ai', false, NULL),
  ('report_tier', 'core', 'physical intelligence', false, NULL),
  ('report_tier', 'core', 'embodied ai', false, NULL),
  ('report_tier', 'core', 'embodied intelligence', false, NULL),
  ('report_tier', 'core', '체화 ai', false, NULL),
  ('report_tier', 'core', '체화지능', false, NULL),
  ('report_tier', 'core', '체화 지능', false, NULL),
  ('report_tier', 'core', '협동로봇', false, NULL),
  ('report_tier', 'core', 'cobot', false, NULL),
  ('report_tier', 'core', '매니퓰레이터', false, NULL),
  ('report_tier', 'core', 'manipulator', false, NULL),
  ('report_tier', 'core', '로봇팔', false, NULL),
  ('report_tier', 'core', 'robot arm', false, NULL),
  ('report_tier', 'core', 'amr', false, NULL),
  ('report_tier', 'core', 'agv', false, NULL),
  ('report_tier', 'core', '자율로봇', false, NULL),
  ('report_tier', 'core', '서비스로봇', false, NULL),
  ('report_tier', 'core', '물류로봇', false, NULL),
  ('report_tier', 'core', '배송로봇', false, NULL),
  ('report_tier', 'core', '의료로봇', false, NULL),
  ('report_tier', 'core', '수술로봇', false, NULL),
  ('report_tier', 'core', '재활로봇', false, NULL),
  ('report_tier', 'core', '돌봄로봇', false, NULL),
  ('report_tier', 'core', '농업로봇', false, NULL),
  ('report_tier', 'core', '국방로봇', false, NULL),
  ('report_tier', 'core', '재난로봇', false, NULL),
  ('report_tier', 'core', '해양로봇', false, NULL),
  ('report_tier', 'core', '수중로봇', false, NULL),
  ('report_tier', 'core', '웨어러블 로봇', false, NULL),
  ('report_tier', 'core', '외골격', false, NULL),
  ('report_tier', 'core', '로봇 액추에이터', false, NULL),
  ('report_tier', 'core', 'robot actuator', false, NULL),
  ('report_tier', 'core', '로봇 감속기', false, NULL),
  ('report_tier', 'core', 'gripper', false, NULL),
  ('report_tier', 'core', '그리퍼', false, NULL),
  ('report_tier', 'core', 'slam', false, NULL),
  ('report_tier', 'core', 'hri', false, NULL),
  ('report_tier', 'core', 'human-robot interaction', false, NULL)
ON CONFLICT (rule_set, kind, term) DO NOTHING;

-- tool: 드론·AI 등 로봇이 도구로 쓰는 인접 영역 — 낮은 우선순위
INSERT INTO public.keyword_rules (rule_set, kind, term, is_regex, note) VALUES
  ('report_tier', 'tool', '드론', false, NULL),
  ('report_tier', 'tool', '무인기', false, NULL),
  ('report_tier', 'tool', 'uav', false, NULL),
  ('report_tier', 'tool', '무인항공', false, NULL),
  ('report_tier', 'tool', '무인이동체', false, NULL),
  ('report_tier', 'tool', '무인선', false, NULL),
  ('report_tier', 'tool', '무인수상정', false, NULL),
  ('report_tier', 'tool', '무인잠수정', false, NULL),
  ('report_tier', 'tool', '자율주행', false, NULL),
  ('report_tier', 'tool', '자율운항', false, NULL),
  ('report_tier', 'tool', '자율제조', false, NULL),
  ('report_tier', 'tool', '인공지능', false, NULL),
  ('report_tier', 'tool', '피지컬 인텔리전스', false, NULL),
  ('report_tier', 'tool', '스마트팩토리', false, NULL),
  ('report_tier', 'tool', '스마트공장', false, NULL),
  ('report_tier', 'tool', '스마트 공장', false, NULL),
  ('report_tier', 'tool', '스마트제조', false, NULL),
  ('report_tier', 'tool', '머신비전', false, NULL)
ON CONFLICT (rule_set, kind, term) DO NOTHING;

-- ------------------------------------------------------------
-- 3. rnd_keywords — NTIS R&D 공고 로봇 관련 판정 (collect_rnd.py)
-- ------------------------------------------------------------

-- keyword: 제목+본문 부분 일치 (recall 우선 — 사용자 지시)
INSERT INTO public.keyword_rules (rule_set, kind, term, is_regex, note) VALUES
  ('rnd_keywords', 'keyword', '로봇', false, NULL),
  ('rnd_keywords', 'keyword', '로보틱스', false, NULL),
  ('rnd_keywords', 'keyword', '자동화', false, NULL),
  ('rnd_keywords', 'keyword', '무인', false, NULL),
  ('rnd_keywords', 'keyword', '드론', false, NULL),
  ('rnd_keywords', 'keyword', '자율', false, '자율주행·자율운항·자율제조·자율행동체 등을 포괄'),
  ('rnd_keywords', 'keyword', '피지컬 ai', false, NULL),
  ('rnd_keywords', 'keyword', '피지컬ai', false, NULL),
  ('rnd_keywords', 'keyword', 'physical ai', false, NULL),
  ('rnd_keywords', 'keyword', 'phisical ai', false, '공고 오탈자 실사례'),
  ('rnd_keywords', 'keyword', '스마트공장', false, NULL),
  ('rnd_keywords', 'keyword', '스마트 공장', false, NULL),
  ('rnd_keywords', 'keyword', '스마트팩토리', false, NULL),
  ('rnd_keywords', 'keyword', '스마트제조', false, NULL),
  ('rnd_keywords', 'keyword', '스마트 제조', false, NULL),
  ('rnd_keywords', 'keyword', 'amr', false, NULL),
  ('rnd_keywords', 'keyword', 'agv', false, NULL),
  ('rnd_keywords', 'keyword', '협동로봇', false, NULL),
  ('rnd_keywords', 'keyword', '휴머노이드', false, NULL),
  ('rnd_keywords', 'keyword', '머신비전', false, NULL),
  ('rnd_keywords', 'keyword', '웨어러블', false, NULL),
  ('rnd_keywords', 'keyword', '외골격', false, NULL),
  ('rnd_keywords', 'keyword', '엑소수트', false, NULL),
  ('rnd_keywords', 'keyword', '근력보조', false, NULL),
  ('rnd_keywords', 'keyword', '착용형', false, NULL),
  ('rnd_keywords', 'keyword', '매니퓰레이터', false, NULL),
  ('rnd_keywords', 'keyword', '그리퍼', false, NULL),
  ('rnd_keywords', 'keyword', '액추에이터', false, NULL),
  ('rnd_keywords', 'keyword', '인공지능', false, NULL),
  ('rnd_keywords', 'keyword', '지능형', false, NULL),
  ('rnd_keywords', 'keyword', '지능화', false, NULL),
  ('rnd_keywords', 'keyword', '디지털트윈', false, NULL),
  ('rnd_keywords', 'keyword', '디지털 트윈', false, NULL),
  ('rnd_keywords', 'keyword', '예지보전', false, NULL),
  ('rnd_keywords', 'keyword', '머신러닝', false, NULL),
  ('rnd_keywords', 'keyword', '제조데이터', false, NULL),
  ('rnd_keywords', 'keyword', '제조 데이터', false, NULL),
  ('rnd_keywords', 'keyword', '공정혁신', false, NULL),
  ('rnd_keywords', 'keyword', '용접', false, NULL),
  ('rnd_keywords', 'keyword', '품질검사', false, NULL),
  ('rnd_keywords', 'keyword', '품질 검사', false, NULL),
  ('rnd_keywords', 'keyword', '재활', false, NULL),
  ('rnd_keywords', 'keyword', '의료기기', false, NULL),
  ('rnd_keywords', 'keyword', '돌봄', false, NULL),
  ('rnd_keywords', 'keyword', '요양', false, NULL),
  ('rnd_keywords', 'keyword', '소방', false, NULL),
  ('rnd_keywords', 'keyword', '화재', false, NULL),
  ('rnd_keywords', 'keyword', '재난', false, NULL),
  ('rnd_keywords', 'keyword', '구조장비', false, NULL),
  ('rnd_keywords', 'keyword', '스마트팜', false, NULL),
  ('rnd_keywords', 'keyword', '농기계', false, NULL),
  ('rnd_keywords', 'keyword', '파종기', false, NULL),
  ('rnd_keywords', 'keyword', '방제', false, NULL),
  ('rnd_keywords', 'keyword', '수중', false, NULL),
  ('rnd_keywords', 'keyword', '수륙양용', false, NULL),
  ('rnd_keywords', 'keyword', '무인선', false, NULL),
  ('rnd_keywords', 'keyword', '무인수상정', false, NULL),
  ('rnd_keywords', 'keyword', '무인잠수정', false, NULL),
  ('rnd_keywords', 'keyword', '모빌리티', false, NULL),
  ('rnd_keywords', 'keyword', '모션 데이터', false, NULL),
  ('rnd_keywords', 'keyword', '모션데이터', false, NULL),
  ('rnd_keywords', 'keyword', '모션캡처', false, NULL),
  ('rnd_keywords', 'keyword', '모션캡쳐', false, NULL)
ON CONFLICT (rule_set, kind, term) DO NOTHING;

-- pattern: 짧은 영문 약어 — 앞뒤에 영문·숫자가 없을 때만 (정규식)
INSERT INTO public.keyword_rules (rule_set, kind, term, is_regex, note) VALUES
  ('rnd_keywords', 'pattern', '(?<![a-z0-9])ai(?![a-z0-9])', true, 'AI 기반·AI기반·Edge AI 등 — 앞뒤에 영문·숫자가 없을 때만'),
  ('rnd_keywords', 'pattern', '(?<![a-z0-9])ax(?![a-z0-9])', true, '제조AX·공정혁신(AX)'),
  ('rnd_keywords', 'pattern', '(?<![a-z0-9])ugv(?![a-z0-9])', true, NULL),
  ('rnd_keywords', 'pattern', '(?<![a-z0-9])uav(?![a-z0-9])', true, NULL),
  ('rnd_keywords', 'pattern', '(?<![a-z0-9])pdm(?![a-z0-9])', true, '예지보전')
ON CONFLICT (rule_set, kind, term) DO NOTHING;

-- ------------------------------------------------------------
-- 4. 본문 최소 길이 설정 — filter_rules.json min_body_length의 DB 버전
-- ------------------------------------------------------------
INSERT INTO public.app_settings (key, value, description) VALUES
  ('filter_min_body_length', '200',
   '뉴스 로컬 필터: 본문이 이 글자 수보다 짧으면 보류(LOW_PRIORITY)로 AI에 넘긴다. /admin/keywords에서 조정')
ON CONFLICT (key) DO NOTHING;

-- ------------------------------------------------------------
-- 5. operation_events.event_type — 고정 목록 CHECK를 형식 검사로 완화
--    관리 기능이 KEYWORD_RULE_CREATE / KEYWORD_RULE_DELETE / KEYWORD_RULE_ENABLE /
--    KEYWORD_RULE_DISABLE / KEYWORD_RULE_SETTING 등 새 유형을 기록한다.
--    목록을 늘리는 대신 '대문자 스네이크'만 허용한다 — 기존 값은 전부 만족.
-- ------------------------------------------------------------
ALTER TABLE public.operation_events
  DROP CONSTRAINT IF EXISTS operation_events_event_type_check;
ALTER TABLE public.operation_events
  ADD CONSTRAINT operation_events_event_type_check
  CHECK (event_type ~ '^[A-Z][A-Z0-9_]*$');
