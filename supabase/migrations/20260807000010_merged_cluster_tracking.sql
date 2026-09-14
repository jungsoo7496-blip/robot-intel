-- ============================================================
-- 0010: 병합된 클러스터 추적 (외부 리뷰 4차)
-- 2차 병합으로 비워진 클러스터가 1차 클러스터링 후보에 계속
-- 등장해 불필요한 재분석 churn을 만드는 문제를 막는다.
-- 물리 삭제 대신 표식을 남겨 분석 이력·병합 추적을 보존한다.
-- ============================================================

ALTER TABLE public.content_clusters
  ADD COLUMN merged_into_cluster_id uuid REFERENCES public.content_clusters (id),
  ADD COLUMN merged_at timestamptz;

CREATE INDEX content_clusters_active_idx
ON public.content_clusters (created_at DESC)
WHERE merged_at IS NULL;

-- 백필: 과거 병합(일괄 정리 포함)으로 구성원이 없어진 클러스터에 표식
UPDATE public.content_clusters c
SET merged_at = now()
WHERE NOT EXISTS (
  SELECT 1 FROM public.cluster_members m WHERE m.cluster_id = c.id
);
