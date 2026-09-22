-- 게시 안 된 클러스터의 부속 보존 기간 (2026-09-22, 운영자 결정 "비우자")
--
-- 금고(vault)는 게시된 기사만 내보낸다. 그래서 AI가 로봇 뉴스가 아니라고 판정했거나
-- 다른 클러스터에 병합돼 카드가 없는 클러스터의 analyses 행과 대표 본문은 어디에도
-- 안 쓰이는데 영구히 남았다 (2026-09-22 실측: analyses 6,078행 12 MiB + 병합분 4,502행
-- 11 MiB + 대표 본문 4 MiB, 하루 +186행). 분석을 하루 7회로 늘리면서 두 배로 늘었다.
--
-- 규칙(cleanup.null_unpublished_bulk): 클러스터 생성 후 N일이 지났고, 카드가 없고,
-- 살아 있는 분석 job이 없으면 analyses 행 삭제 + 대표 raw_item 본문 NULL.
-- 클러스터 행·raw_items 행(제목·링크·수집 기록)·cluster_members는 남긴다 — 기사 자체는
-- 안 지운다. 0이면 규칙이 꺼진다.

INSERT INTO public.app_settings (key, value, description)
VALUES (
  'unpublished_retention_days',
  '30',
  '게시 안 된 클러스터의 AI 분석 결과·대표 본문을 며칠 뒤 비울지 (0=안 함). 제목·링크·수집 기록은 남긴다'
)
ON CONFLICT (key) DO NOTHING;

-- analyses 행을 지울 때마다 FK(published_items.current_analysis_id ON DELETE SET NULL)
-- 검사가 published_items를 처음부터 훑는다 — 금고 정리도 같은 삭제를 한다. 색인 하나로
-- 끝낸다 (카드 3만여 행, 1 MB 미만).
CREATE INDEX IF NOT EXISTS published_items_current_analysis_idx
  ON public.published_items (current_analysis_id);

-- "이 raw_item을 대표로 둔 다른 클러스터가 있나" — 금고 정리(C5)와 이 규칙이 행마다 묻는다.
CREATE INDEX IF NOT EXISTS content_clusters_representative_idx
  ON public.content_clusters (representative_raw_item_id);
