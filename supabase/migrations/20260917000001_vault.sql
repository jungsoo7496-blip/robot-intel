-- ============================================================
-- 0027 (2026-09-17): 파일 금고(vault) — DB 500 MB 한도 대응, 기사는 하나도 안 지운다
--
-- 화면이 실제로 그리는 '카드'(published_items·content_clusters·cluster_members·
-- policy_details)는 DB에 영구히 남기고, 무거운 부속(그 클러스터의 analyses 전부·끝난
-- analysis_jobs·raw_items 본문)은 반달(半月) 단위 파일로 Storage 비공개 버킷 'vault'에
-- 옮긴 뒤 DB에서 지운다 (scripts/kiro_batch/vault.py). 이 파일은 그 준비 작업이다.
--
-- 1) published_items.current_analysis_id — NULL 허용, FK ON DELETE SET NULL
--    (금고로 옮긴 뒤 analyses 행을 지우면 자동으로 NULL이 된다)
-- 2) published_items.vaulted_at — 부속이 금고로 옮겨져 DB에서 지워진 시각.
--    화면은 이 값이 있으면 금고 파일에서 읽는다.
--    ※ 부분 인덱스는 만들지 않는다 — 카드 표 인덱스 비대 방지.
-- 3) published_items.kiro_axes — 업무축(analyses.kiro_relevance_axes 사본, C7).
--    화면의 업무축 필터가 analyses inner join 대신 이 컬럼을 쓴다 — 금고로 옮겨
--    analyses가 지워진 카드가 목록에서 사라지지 않게. 현재 current 분석으로 백필하고
--    publish.py가 카드 INSERT/UPDATE 때 채운다.
-- 4) vault_manifest — 기간별 파일 대장 (service role 전용, RLS 정책 없음)
--    month = 'YYYY-MM-a'(KST 1~15일) | 'YYYY-MM-b'(16일~말일) — 컬럼 이름은 month지만
--    값은 반달 기간 키(C1). analysis_ids = 파일에 들어간 분석 id 전부(C2),
--    verify_failures = 검증 실패 누적(3이면 운영자가 0으로 되돌릴 때까지 재시도 안 함, C9)
-- 5) app_settings — vault_window_days(14, 하한 14), vault_prune_enabled(false)
-- 전부 additive·멱등 — 두 번 적용해도 같은 결과. 기존 데이터·동작에 영향 없음.
-- ============================================================

-- ------------------------------------------------------------
-- 1. published_items.current_analysis_id — NOT NULL 해제 + FK 재생성
-- ------------------------------------------------------------
ALTER TABLE public.published_items
  ALTER COLUMN current_analysis_id DROP NOT NULL;

-- analyses를 가리키는 FK는 이름이 환경마다 다를 수 있어 카탈로그에서 찾아 지운다
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'public.published_items'::regclass
      AND contype = 'f'
      AND confrelid = 'public.analyses'::regclass
  LOOP
    EXECUTE format('ALTER TABLE public.published_items DROP CONSTRAINT %I', r.conname);
  END LOOP;
END $$;

ALTER TABLE public.published_items
  ADD CONSTRAINT published_items_current_analysis_id_fkey
  FOREIGN KEY (current_analysis_id) REFERENCES public.analyses (id) ON DELETE SET NULL;

-- ------------------------------------------------------------
-- 2. published_items.vaulted_at
-- ------------------------------------------------------------
ALTER TABLE public.published_items
  ADD COLUMN IF NOT EXISTS vaulted_at timestamptz;

COMMENT ON COLUMN public.published_items.vaulted_at IS
  '부속(analyses·본문)이 금고 파일로 옮겨져 DB에서 지워진 시각. 값이 있으면 화면은 금고에서 읽는다. cluster_members는 남는다';

-- ------------------------------------------------------------
-- 3. published_items.kiro_axes — 업무축 사본 (C7)
-- ------------------------------------------------------------
ALTER TABLE public.published_items
  ADD COLUMN IF NOT EXISTS kiro_axes jsonb;

COMMENT ON COLUMN public.published_items.kiro_axes IS
  'KIRO 업무축(analyses.kiro_relevance_axes 사본). publish.py가 카드 저장 때 채우고, 화면의 업무축 필터가 analyses 대신 이 컬럼을 읽는다 (금고 카드도 목록에 남게). NULL = 아직 안 채워짐';

-- 백필: 현재 current 분석의 값으로. 두 번째 적용에서는 이미 같은 값이라 0행
UPDATE public.published_items p
SET kiro_axes = a.kiro_relevance_axes
FROM public.analyses a
WHERE a.id = p.current_analysis_id
  AND p.kiro_axes IS DISTINCT FROM a.kiro_relevance_axes;

-- 업무축 필터(@> 포함 검색)용 — analyses에 있던 것과 같은 꼴. jsonb_path_ops는 작다
CREATE INDEX IF NOT EXISTS published_items_kiro_axes_gin_idx
  ON public.published_items USING gin (kiro_axes jsonb_path_ops);

-- ------------------------------------------------------------
-- 4. vault_manifest — 기간별 파일 대장
--    month = 반달 기간 키 'YYYY-MM-a' | 'YYYY-MM-b' (published_at을 Asia/Seoul로)
--    storage_path = items/YYYY-MM-a.jsonl.gz (같은 이름의 .idx.json이 짝)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.vault_manifest (
  month                text PRIMARY KEY,
  rows                 int NOT NULL,
  bytes                bigint NOT NULL,
  sha256               text NOT NULL,
  storage_path         text NOT NULL,
  sample_ids           jsonb NOT NULL,
  analysis_ids         jsonb NOT NULL DEFAULT '{}'::jsonb,
  exported_at          timestamptz NOT NULL,
  storage_verified_at  timestamptz,
  release_verified_at  timestamptz,
  release_tag          text,
  pruned_at            timestamptz,
  pruned_rows          int,
  verify_failures      int NOT NULL DEFAULT 0
);

-- 이전 초안(월 키 '^\d{4}-\d{2}$')으로 만들어진 표도 같은 모양으로 맞춘다
ALTER TABLE public.vault_manifest
  ADD COLUMN IF NOT EXISTS analysis_ids jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.vault_manifest
  ADD COLUMN IF NOT EXISTS verify_failures int NOT NULL DEFAULT 0;
ALTER TABLE public.vault_manifest
  DROP CONSTRAINT IF EXISTS vault_manifest_month_check;
ALTER TABLE public.vault_manifest
  ADD CONSTRAINT vault_manifest_month_check CHECK (month ~ '^\d{4}-\d{2}-[ab]$');

COMMENT ON TABLE public.vault_manifest IS
  '금고 기간별 파일 대장 (month = 반달 기간 키 YYYY-MM-a|b). exported_at → storage_verified_at(재다운로드 대조) → release_verified_at(GitHub Release 미러) → pruned_at(DB 부속 삭제 완료) 순으로 채워진다';
COMMENT ON COLUMN public.vault_manifest.analysis_ids IS
  '{"<published_item_id>": ["<analysis id>", ...]} — 파일에 들어간 분석 id 전부(current 포함). prune은 여기 있는 id만 지우고, 카드의 current가 목록에 없으면 그 카드는 건너뛴다';
COMMENT ON COLUMN public.vault_manifest.verify_failures IS
  '재다운로드 검증 실패 누적. 3 이상이면 배치가 매일 재시도하지 않는다(VAULT_VERIFY_STALLED). 운영자가 0으로 되돌리면 재개. 검증 통과 시 0';

-- service role(배치·서버)만 접근 — 정책을 만들지 않으면 anon·authenticated는 전부 거부
ALTER TABLE public.vault_manifest ENABLE ROW LEVEL SECURITY;

-- ------------------------------------------------------------
-- 5. 설정 (cleanup 배치가 읽는다 — 값은 jsonb)
-- ------------------------------------------------------------
INSERT INTO public.app_settings (key, value, description) VALUES
  ('vault_window_days', '14',
   '반달 기간(1~15일 / 16일~말일)의 마지막 날 뒤 며칠이 지나야 그 기간을 금고 파일로 내보낼지. 하한 14(병합 창 7일의 2배 — 더 작게 두면 배치가 14로 올린다). 오늘이 속한 기간은 절대 대상이 아님'),
  ('vault_prune_enabled', 'false',
   '금고로 옮기고 검증(Storage·Release 둘 다)이 끝난 기간의 부속을 DB에서 지울지 (false=내보내기·검증만)')
ON CONFLICT (key) DO NOTHING;
