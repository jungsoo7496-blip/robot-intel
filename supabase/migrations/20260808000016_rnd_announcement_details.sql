-- 0016: R&D 공고 상세 필드 (사용자 피드백 2026-08-08 — "제목만 가져오냐")
--
-- NTIS 공고 상세 페이지에서 구조화 필드를 추가 수집한다.
-- 키워드 매칭도 제목+본문으로 확장 (로봇 관련이면 폭넓게 — recall 우선).

ALTER TABLE public.rnd_announcements
  ADD COLUMN apply_start date,          -- 접수 시작일
  ADD COLUMN notice_type text,          -- 공고유형 (신규과제·수요조사 등)
  ADD COLUMN budget_text text,          -- 공고금액 표기 (0/미상은 NULL)
  ADD COLUMN org_name text,             -- 공고기관명 (전문기관)
  ADD COLUMN description text,          -- 공고내용 본문 요약 (앞부분)
  ADD COLUMN status_label text;         -- 접수중 | 접수예정
