-- 데이터 관리 (2026-09-08, feature/admin-management 사용자 요구 6번)
-- 1) raw_items.extract_status에 'EXPIRED' 허용 — collect.py가 발행(없으면 수집)
--    N일(extract_expire_days) 초과 추출 대기를 만료시킨다. cleanup.py가 90일 뒤 행 삭제.
-- 2) 대기 선정·만료 질의용 부분 인덱스 (coalesce(published_at, fetched_at) 정렬)
-- 3) operation_events.event_type CHECK를 열거형에서 형식 검사로 —
--    DATA_RETENTION_UPDATE, DATA_CLEANUP_DISPATCH 등 화면마다 새 조치 유형이
--    생길 때마다 마이그레이션을 내지 않도록 대문자 스네이크면 허용한다.
-- 전부 추가·완화만 — 기존 행·동작에 영향 없음.

-- ------------------------------------------------------------
-- 1. raw_items.extract_status CHECK 교체
--    (컬럼 인라인 CHECK의 자동 이름은 raw_items_extract_status_check이지만,
--     이름이 달라도 놓치지 않도록 정의 내용으로 찾아 지운다)
-- ------------------------------------------------------------
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'public.raw_items'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%extract_status%'
  LOOP
    EXECUTE format('ALTER TABLE public.raw_items DROP CONSTRAINT %I', r.conname);
  END LOOP;
END $$;

ALTER TABLE public.raw_items
  ADD CONSTRAINT raw_items_extract_status_check
  CHECK (extract_status IN ('PENDING', 'OK', 'FAILED', 'META_ONLY', 'EXPIRED'));

COMMENT ON COLUMN public.raw_items.extract_status IS
  'PENDING 대기 / OK 본문 확보 / META_ONLY 본문 없음 / FAILED 내려받기 실패 / '
  'EXPIRED 발행 N일 초과로 만료 (extract_expire_days, 본문 추출·분석 안 함)';

-- 대기 선정(collect.process_new_items)과 만료(expire_stale_pending)가 같은 키로 읽는다
CREATE INDEX IF NOT EXISTS raw_items_pending_order_idx
  ON public.raw_items ((coalesce(published_at, fetched_at)))
  WHERE extract_status = 'PENDING';

-- ------------------------------------------------------------
-- 2. operation_events.event_type — 열거 CHECK → 형식 CHECK
--    (운영 DB에는 2026-09-08 확인 시 이미 이 형식 CHECK가 적용돼 있었다 —
--     저장소의 마이그레이션에 없던 변경이라 여기서 같은 정의로 고정한다. 멱등)
-- ------------------------------------------------------------
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'public.operation_events'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%event_type%'
  LOOP
    EXECUTE format('ALTER TABLE public.operation_events DROP CONSTRAINT %I', r.conname);
  END LOOP;
END $$;

ALTER TABLE public.operation_events
  ADD CONSTRAINT operation_events_event_type_check
  CHECK (event_type ~ '^[A-Z][A-Z0-9_]*$');

COMMENT ON COLUMN public.operation_events.event_type IS
  '운영 조치 유형 — 대문자 스네이크 (예: SOURCE_ENABLE, DATA_RETENTION_UPDATE, DATA_CLEANUP_DISPATCH, OTHER)';
