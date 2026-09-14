-- ============================================================
-- 초기 수집원 시드 (tasks §4.2, 외부 리뷰 P0-5)
-- 새 환경에서도 동일한 수집원이 재현되도록 버전 관리한다.
-- idempotent: url UNIQUE 기준 upsert. 운영 중 토글한 is_active는 건드리지 않는다.
--
-- 여기에 **네이버·구글 뉴스 검색 수집원은 없다** (2026-09-08 검색 키워드 통합).
-- 검색어는 search_keywords 표 한 곳에만 있고(마이그레이션 26이 9개를 시드한다),
-- 그 검색어를 실제 sources 행으로 만드는 곳은 수집 배치의
-- sync_news_search_sources 하나뿐이다(scripts/kiro_batch/search_keywords.py).
-- 시드가 같은 행을 미리 만들면 배치가 만드는 구글 RSS 주소와 url이 겹쳐
-- (sources_url_key) 조합이 조용히 건너뛰어지므로 넣지 않는다.
--   · 검색 수집원은 첫 뉴스 수집 배치(한국시각 06:20·14:20·22:10)에서 생긴다.
--   · NAVER_CLIENT_ID/SECRET가 없는 환경이면 검색 키워드 화면에서
--     '네이버 뉴스 검색' 대상을 꺼 두면 네이버 행이 만들어지지 않는다.
-- ============================================================

INSERT INTO public.sources
  (name, source_type, country_region, language, url,
   fetch_method, adapter_config, priority, is_active, notes)
VALUES
  ('정책브리핑 정책뉴스 (korea.kr)', '정부·공공기관', '국내', 'ko',
   'https://www.korea.kr/news/policyNewsList.do', 'LIST_PAGE',
   '{"item_selector": "li > a[href*=''policyNewsView.do'']", "title_selector": "strong", "summary_selector": "span.lead", "base_url": "https://www.korea.kr"}'::jsonb,
   10, true, 'korea.kr RSS 중단으로 목록 페이지 수집'),

  ('로봇신문', '로봇·산업 전문매체', '국내', 'ko',
   'http://www.irobotnews.com/rss/allArticle.xml', 'RSS', NULL, 30, true,
   '초기 시드 (tasks 4.2)'),

  ('IEEE Spectrum Robotics', '전문 기술 큐레이션 매체', '미국', 'en',
   'https://spectrum.ieee.org/feeds/topic/robotics.rss', 'RSS', NULL, 30, true,
   '초기 시드 (tasks 4.2)'),

  ('The Robot Report', '로봇·산업 전문매체', '미국', 'en',
   'https://www.therobotreport.com/feed/', 'RSS', NULL, 30, true,
   '초기 시드 (tasks 4.2)'),

  ('Robohub', '전문 기술 큐레이션 매체', '기타', 'en',
   'https://robohub.org/feed/', 'RSS', NULL, 30, true,
   '초기 시드 (tasks 4.2)')

-- 구글 뉴스 검색 4행(로봇·휴머노이드·로보틱스·피지컬 AI)은 여기서 뺐다.
-- search_keywords의 같은 검색어로 수집 배치가 똑같은 주소를 만든다.

ON CONFLICT (url) DO UPDATE SET
  name = EXCLUDED.name,
  source_type = EXCLUDED.source_type,
  country_region = EXCLUDED.country_region,
  language = EXCLUDED.language,
  fetch_method = EXCLUDED.fetch_method,
  adapter_config = EXCLUDED.adapter_config,
  priority = EXCLUDED.priority,
  notes = EXCLUDED.notes;

-- ------------------------------------------------------------
-- 어댑터 미구현 핵심 수집원 (외부 리뷰 2차 — 커버리지 누락 관리)
-- is_active=false로 등록만 해 두고, 어댑터 구현 후 활성화한다.
-- ------------------------------------------------------------
INSERT INTO public.sources
  (name, source_type, country_region, language, url,
   fetch_method, priority, is_active, notes)
VALUES
  ('산업통상자원부 보도자료', '정부·공공기관', '국내', 'ko',
   'https://www.motie.go.kr/kor/article/ATCL3f49a5a8c', 'PREDEFINED', 10, false,
   'PENDING_ADAPTER — 목록 페이지 어댑터 구현 필요'),
  ('과학기술정보통신부 보도자료', '정부·공공기관', '국내', 'ko',
   'https://www.msit.go.kr/bbs/list.do?sCode=user&mId=113&mPid=112', 'PREDEFINED', 10, false,
   'PENDING_ADAPTER'),
  ('중소벤처기업부 보도자료', '정부·공공기관', '국내', 'ko',
   'https://www.mss.go.kr/site/smba/ex/bbs/List.do?cbIdx=86', 'PREDEFINED', 10, false,
   'PENDING_ADAPTER'),
  ('한국산업기술진흥원(KIAT) 공고', '정책·사업 공고', '국내', 'ko',
   'https://www.kiat.or.kr/front/board/boardContentsListPage.do?board_id=90', 'PREDEFINED', 10, false,
   'PENDING_ADAPTER'),
  ('한국산업기술기획평가원(KEIT) 공고', '정책·사업 공고', '국내', 'ko',
   'https://www.keit.re.kr/board.es?mid=a10305010000&bid=0008', 'PREDEFINED', 10, false,
   'PENDING_ADAPTER'),
  ('한국로봇산업진흥원(KIRIA) 공고', '정책·사업 공고', '국내', 'ko',
   'https://www.kiria.org/portal/notify/portalNotifyBusinessList.do', 'PREDEFINED', 10, false,
   'PENDING_ADAPTER'),
  ('한국AI·로봇산업협회 소식', '연구기관·대학', '국내', 'ko',
   'https://www.korearobot.or.kr/', 'PREDEFINED', 20, false,
   'PENDING_ADAPTER'),
  ('월간로봇기술', '로봇·산업 전문매체', '국내', 'ko',
   'https://www.robotzine.co.kr/', 'PREDEFINED', 30, false,
   'PENDING_ADAPTER'),
  ('전자신문', '일반 언론', '국내', 'ko',
   'https://www.etnews.com/', 'PREDEFINED', 40, false,
   'PENDING_ADAPTER — RSS 주소 확인 필요'),
  ('마로솔 로봇 트렌드', '전문 기술 큐레이션 매체', '국내', 'ko',
   'https://www.myrobotsolution.com/contents/trend', 'PREDEFINED', 30, false,
   'PENDING_ADAPTER')

-- 네이버 뉴스 검색 5행(로봇·휴머노이드·로봇 정책·한국로봇융합연구원·피지컬 AI)도
-- 여기서 뺐다. 검색어와 조회 건수(display)는 이제 search_keywords와
-- news_search_targets가 가지고 있고, 행은 수집 배치가 만든다.
ON CONFLICT (url) DO NOTHING;
