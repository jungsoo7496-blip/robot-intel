-- 0021: 분석 큐에 원문 발행시각 비정규화 — 신선도 우선 처리 (2026-08-11)
--
-- 사용자 제보: "아침에 보는 기사가 다 어제 것이다."
-- 원인은 acquire_next_job이 FIFO(created_at ASC)라 아침 배치가 언제나
-- 전날 저녁 적재분부터 처리하고 예산이 끝나는 것이었다. 실측으로 08-10 08:04
-- 배치가 처리한 13건은 전부 08-09 17:13 적재분이었고, 그 사이 실행된
-- 04:17·07:17 수집분(85건)은 한 건도 건드리지 못했다.
--
-- 정렬에 원문 발행시각이 필요한데 매번 content_clusters→raw_items를 조인하면
-- 잠금 쿼리가 무거워지므로 큐에 비정규화한다.

ALTER TABLE public.analysis_jobs
  ADD COLUMN IF NOT EXISTS source_published_at timestamptz;

COMMENT ON COLUMN public.analysis_jobs.source_published_at IS
  '대표 기사의 원문 발행시각 (미래 표기는 적재 시각으로 클램프). 신선도 정렬용 비정규화.';

-- 기존 큐 백필: 대표 원문의 발행시각, 없으면 적재 시각.
-- LEAST는 NULL을 무시하므로 미래 표기 클램프가 함께 적용된다.
UPDATE public.analysis_jobs j
   SET source_published_at = LEAST(ri.published_at, j.created_at)
  FROM public.content_clusters c
  JOIN public.raw_items ri ON ri.id = c.representative_raw_item_id
 WHERE c.id = j.cluster_id
   AND j.source_published_at IS NULL;

-- 발행시각을 모르는 잔여분은 적재 시각으로 채운다. NULL로 남기면 정렬에서
-- 영구 최하위가 되고 만료·기아 방지 어느 쪽에도 참여하지 못한다.
UPDATE public.analysis_jobs
   SET source_published_at = created_at
 WHERE source_published_at IS NULL;

-- 잠금 쿼리 전용 인덱스. 계층(priority/10*10) 안에서 신선도 역순.
CREATE INDEX IF NOT EXISTS analysis_jobs_fresh_idx
  ON public.analysis_jobs (((priority / 10) * 10), source_published_at DESC)
  WHERE status IN ('PENDING', 'RETRY', 'DEFERRED');

-- 기아 방지(오래 기다린 것 우선) 경로용
CREATE INDEX IF NOT EXISTS analysis_jobs_oldest_idx
  ON public.analysis_jobs (((priority / 10) * 10), created_at)
  WHERE status IN ('PENDING', 'RETRY', 'DEFERRED');
