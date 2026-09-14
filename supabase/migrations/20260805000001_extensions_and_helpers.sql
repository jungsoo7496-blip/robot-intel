-- ============================================================
-- 0001: 확장과 공통 헬퍼
-- 관련 설계: design v0.3 §10, §11 / tasks v0.2 §2.2
-- ============================================================

-- 한국어 부분 문자열 검색 (FR-011)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- updated_at 자동 갱신 트리거 함수
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

-- RLS 헬퍼 함수(is_active_user, is_operator)는 profiles 테이블 생성 이후인
-- 0004_rls.sql 에서 정의한다.
