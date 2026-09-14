# KIRO 로봇 인텔리전스 플랫폼 설계서

- 문서 버전: v0.3
- 작성일: 2026-08-04
- 기준 요구사항: `KIRO_robot_intelligence_requirements_v0.2.md`
- 대상 단계: 완전 무료 프로토타입 / Phase 1 MVP
- 배포 대상: KIRO 내부 직원
- 운영 전제: 전담 분석가 없음, 운영 책임자 월 1~2시간 이내
- AI 전제: Gemini API 무료 티어
- 저장소 전제: 비공개 GitHub 저장소
- 문서 상태: tasks.md 작성 전 확정 설계

---

# 1. 설계 목표

본 설계는 다음 조건을 동시에 만족하는 것을 목표로 한다.

1. 무료 티어 범위에서 실행 가능한 프로토타입을 구축한다.
2. 정상 운영에 상시 분석 담당자가 필요하지 않도록 한다.
3. 운영 책임자의 정기 관리 시간을 월 1~2시간 이내로 제한한다.
4. 공개된 로봇 정책·산업·기술 자료를 자동으로 수집·선별·분석한다.
5. 확인된 사실, AI 해석, KIRO 시사점을 명확히 분리한다.
6. 동일하거나 매우 유사한 기사가 반복 노출되지 않도록 한다.
7. 기존 KIRO 보고서 골격을 유지한 격주 브리프를 자동 발행한다.
8. 특정 뉴스 사이트나 특정 Gemini 모델에 종속되지 않도록 한다.
9. 무료 API의 RPM·TPM·RPD와 GitHub Actions 실행시간을 모두 예산화한다.
10. 한국어 키워드 검색과 정책·R&D 구조화 탐색을 실제 구현 가능한 수준으로 제공한다.
11. 배치 중단·잠금 잔류·일일 한도 초과가 작업 유실로 이어지지 않도록 한다.
12. Phase 2 기능을 추가할 수 있도록 핵심 데이터 구조를 확장 가능하게 유지한다.

---

# 2. v0.2 주요 변경사항

v0.1 대비 다음을 수정한다.

- GitHub 저장소를 비공개로 확정
- Actions 월간 내부 예산 1,500분 설정
- 수집·분석 job에 `timeout-minutes: 5` 강제
- 30분 이상 남아 있는 `PROCESSING` 잠금을 매 분석 배치 시작 시 자동 복구
- 한국어 검색에 `pg_trgm` GIN 인덱스 사용
- FR-010 구현을 위한 `policy_details` 테이블과 `policy_meta` AI 출력 추가
- 브리프를 1회 장문 생성이 아닌 3개 섹션 호출 후 코드 조립 방식으로 변경
- 수동 URL 등록 API와 화면 추가
- 가입 허용 이메일 도메인을 설정값으로 관리
- 핵심 테이블 주간 논리 백업 추가
- GitHub Actions와 Supabase의 마지막 정상 실행 시각을 운영 화면에서 감시
- 공개 저장소 전환 시에만 예약 워크플로 비활성화 위험을 별도 검토
- 장기 무인 운영 보험으로 마지막 커밋 45일 경과 시 keepalive 적용
- Gemini 일일 쿼터 집계를 `America/Los_Angeles` 날짜 기준으로 처리
- 수집·정제·클러스터·분석 단계별 실행시간 계측 추가
- 파일럿 처리량 부족 시 수집 job과 분석 job 분리 기준 추가
- 백업 artifact 보존기간을 28일, 최근 4개 수준으로 제한

---

# 3. 설계 원칙

## 3.1 무료 우선

Phase 1 기본 구성:

- 웹 애플리케이션: 무료 정적·서버리스 호스팅
- 데이터베이스·인증: Supabase Free
- 배치 스케줄러: GitHub Actions
- AI 분석: Gemini API Free Tier
- 검색: PostgreSQL + `pg_trgm`
- 백업: GitHub Actions를 이용한 핵심 테이블 논리 덤프

## 3.2 배치 우선

- 수집 주기: 기본 3시간
- AI 분석: DB 작업 큐 기반 순차 처리
- 미처리 작업: 다음 배치로 이월
- API 한도 초과: 실패가 아니라 지연으로 처리
- 격주 브리프: 일반 기사 분석보다 우선 처리
- GitHub Actions 예약 실행 지연을 허용

## 3.3 실행시간 예산 우선

비공개 저장소의 무료 Actions 실행시간을 고려해 다음 한도를 둔다.

```text
수집·분석: 하루 8회 × 실행당 최대 5분
이론상 월 최대: 약 1,200분

브리프·백업·수동 재실행 여유 포함
월간 내부 예산: 1,500분
```

운영 화면에서 월간 누적 실행시간을 확인한다.

내부 예산의 80%에 도달하면 다음 중 하나를 적용한다.

1. 수집 주기 3시간 → 4시간
2. 배치당 처리 건수 40건 → 30건
3. 원문 수집과 AI 분석을 하나의 job으로 통합
4. 불필요한 수동 재실행 제한

## 3.4 단순성 우선

Phase 1에서는 Redis, RabbitMQ, Kafka, 벡터 DB, 지식 그래프, 다중 LLM 공급자 자동 전환을 사용하지 않는다.

대신 PostgreSQL 작업 큐, URL·문자열 유사도 기반 클러스터링, `pg_trgm` 기반 한국어 부분 검색, Gemini 단일 공급자를 사용한다.

## 3.5 AI 단일 구조화 호출 우선

기사 하나에 대해 가능한 한 한 번의 호출로 다음을 생성한다.

- 로봇 관련성
- 분야·지역·로봇 분야
- 핵심 사실
- 정책·R&D 메타데이터
- 한 문장 요약
- AI 해석
- KIRO 시사점
- 중요도·근거 수준·KIRO 관련성

## 3.6 실패 허용

특정 수집원 장애, 기사 본문 추출 실패, Gemini 한도 초과, AI JSON 파싱 실패, GitHub Actions 중간 종료, 브리프 일부 섹션 생성 실패가 기존 콘텐츠 열람과 검색을 중단시키지 않아야 한다.

---

# 4. 전체 시스템 구조

```text
KIRO 직원 브라우저
        │
        ▼
내부 웹 애플리케이션
홈 / 최신 동향 / 정책·R&D / 브리프 / 아카이브 / 검색
운영 현황 / 수집원 / 수동 URL / 오류 신고 / 재분석
        │
        ▼
Supabase PostgreSQL
sources / raw_items / content_clusters / analysis_jobs
analyses / policy_details / published_items / briefs
error_reports / app_settings / workflow_usage
        │
        ├───────────────┐
        ▼               ▼
GitHub Actions          Gemini API Free Tier
수집·클러스터·분석      Flash Lite: 기사 분석
브리프·잠금복구·백업    Flash: 브리프 종합
```

---

# 5. 권장 기술 스택

## 5.1 프론트엔드

- Next.js
- TypeScript
- Tailwind CSS
- Supabase JavaScript SDK

## 5.2 배치·크롤러

- Python
- `httpx` 또는 `requests`
- `feedparser`
- `BeautifulSoup` 또는 `selectolax`
- `trafilatura`
- `rapidfuzz`
- Google Gemini SDK
- Supabase Python SDK 또는 PostgreSQL 직접 연결

## 5.3 데이터베이스

- Supabase PostgreSQL
- Supabase Auth
- Row Level Security
- `pg_trgm`
- PostgreSQL JSONB
- PostgreSQL 작업 큐

## 5.4 스케줄러

```yaml
on:
  schedule:
    - cron: "17 */3 * * *"
  workflow_dispatch:

jobs:
  collect-and-analyze:
    runs-on: ubuntu-latest
    timeout-minutes: 5
```

---

# 6. 무료 인프라 운영 설계

## 6.1 저장소

Phase 1은 비공개 GitHub 저장소를 사용한다.

- 수집원 목록 비공개
- KIRO 시사점 생성 프롬프트 비공개
- 내부 운영 구조 비공개
- API 키는 GitHub Secrets에 저장

공개 저장소로 변경할 경우 예약 워크플로 비활성화 정책과 프롬프트·수집원 공개 가능 여부를 별도 검토한다.

## 6.2 Actions 실행시간 예산

```text
월간 내부 상한: 1,500분
경고 기준: 1,200분
```

한 워크플로 안에서 다음을 연속 실행한다.

```text
수집
→ 로컬 필터
→ 중복 클러스터링
→ 잠금 복구
→ AI 큐 처리
```

## 6.3 Supabase 일시 중지 대응

운영 화면에 다음을 표시한다.

- 마지막 수집 배치 성공 시각
- 마지막 분석 배치 성공 시각
- 마지막 DB 쓰기 시각
- 24시간 이상 배치 미실행 경고
- 수동 실행 안내

## 6.4 예약 워크플로 keepalive

GitHub의 저장소 유형별 예약 워크플로 비활성화 정책 해석과 무관하게, 장기 무인 운영의 방어적 조치로 keepalive를 적용한다.

주간 `backup.yml` 또는 별도 `keepalive.yml`에서 다음을 수행한다.

```text
마지막 기본 브랜치 커밋 시각 확인
→ 45일 미만이면 종료
→ 45일 이상이면 .github/keepalive.txt 갱신
→ github-actions[bot] 계정으로 자동 커밋
→ 성공·실패를 workflow_usage에 기록
```

예시:

```yaml
permissions:
  contents: write

- uses: actions/checkout@v4
  with:
    fetch-depth: 0

- name: Refresh repository activity
  shell: bash
  run: |
    LAST_COMMIT_TS=$(git log -1 --format=%ct)
    NOW_TS=$(date +%s)
    AGE_DAYS=$(( (NOW_TS - LAST_COMMIT_TS) / 86400 ))

    if [ "$AGE_DAYS" -ge 45 ]; then
      date -u +"%Y-%m-%dT%H:%M:%SZ" > .github/keepalive.txt
      git config user.name "github-actions[bot]"
      git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
      git add .github/keepalive.txt
      git commit -m "chore: refresh scheduled workflows [skip ci]"
      git push
    fi
```

추가 조건:

- 최근 45일 이내 사람이 커밋했다면 아무 작업도 하지 않는다.
- keepalive 파일 변경으로 배포·테스트가 불필요하게 실행되지 않도록 `paths-ignore`를 사용한다.
- 기본 브랜치 보호 규칙이 봇 커밋을 차단하는지 파일럿 전에 확인한다.
- 운영 화면에 마지막 기본 브랜치 활동 시각과 마지막 keepalive 성공 시각을 표시한다.

---

# 7. AI 모델 라우팅

```text
ARTICLE_MODEL
- 기본: Gemini 3.1 Flash Lite
- 기사 관련성·분류·사실·요약·시사점

BRIEF_MODEL
- 기본: Gemini 3 Flash
- 대안: Gemini 2.5 Flash
- 브리프 섹션 종합

BRIEF_FALLBACK_MODEL
- 기본: ARTICLE_MODEL
- Flash 한도 부족 시 축소 생성
```

초기 내부 운영값:

```text
Flash Lite
- 일일 목표: 320~350회
- 배치당 최대: 40회
- 호출 간격: 5초

Flash 또는 2.5 Flash
- 브리프 발행일 최대 5회
```

## 7.1 일일 쿼터 기준시각

Gemini RPD 내부 카운터는 한국시간 자정이 아니라 `America/Los_Angeles` 기준 날짜를 사용한다.

```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

quota_date_pt = datetime.now(timezone.utc).astimezone(
    ZoneInfo("America/Los_Angeles")
).date()
```

태평양 표준시·서머타임 전환은 IANA 타임존 데이터가 처리하도록 하며, KST 오후 4시 또는 5시를 코드에 고정하지 않는다.

호출 기록에는 다음을 포함한다.

```text
model_name
quota_date_pt
called_at_utc
request_count
status
```

`일일 내부 목표 도달` 여부도 `quota_date_pt` 기준으로 계산한다.

---

# 8. 처리 파이프라인

```text
SOURCE_DISCOVERY
→ FETCH
→ NORMALIZE
→ LOCAL_PREFILTER
→ DUPLICATE_CHECK
→ CLUSTER
→ ENQUEUE
→ STALE_LOCK_RECOVERY
→ AI_ANALYSIS
→ VALIDATE
→ POLICY_NORMALIZE
→ PUBLISH
```

## 8.1 입력 방식

- RSS·Atom
- 공개 목록 페이지
- 사전 정의 수집기
- 운영자 수동 URL

## 8.2 URL 정규화

- UTM·추적 파라미터 제거
- 세션 파라미터 제거
- URL 프래그먼트 제거
- 마지막 슬래시 정규화
- HTTP/HTTPS 정규화
- AMP·모바일 변형 정규화

`canonical_url`에 UNIQUE 제약을 적용한다.

## 8.3 로컬 필터

Gemini 호출 전에 명백한 광고·채용·단순 행사·주가 단독·본문 부족·동일 URL 콘텐츠를 제외한다.

## 8.4 단순 중복 클러스터링

```text
URL 동일
OR
제목 유사도 높음
AND 발표일 차이 3일 이내
AND 기관·기업·정책명 중 하나 이상 일치
```

대표 자료 우선순위:

```text
정부·공공기관
→ 기업·연구기관 공식자료
→ 전문매체
→ 일반 언론
```

---

# 9. 작업 큐 설계

## 9.1 상태

```text
PENDING
PROCESSING
RETRY
DEFERRED
DONE
FAILED
CANCELLED
```

## 9.2 우선순위

```text
0  격주 브리프
10 정부·공공기관
20 기업·연구기관
30 전문 기술·로봇 매체
40 일반 뉴스
```

## 9.3 오래된 잠금 복구

매 분석 배치 시작 시 실행한다.

```sql
UPDATE analysis_jobs
SET
  status = 'RETRY',
  locked_at = NULL,
  locked_by = NULL,
  available_at = now(),
  last_error_code = 'STALE_LOCK',
  last_error_message = '30분 이상 처리 상태가 유지되어 자동 복구됨'
WHERE status = 'PROCESSING'
  AND locked_at < now() - interval '30 minutes';
```

## 9.4 작업 잠금

```sql
SELECT id
FROM analysis_jobs
WHERE status IN ('PENDING', 'RETRY', 'DEFERRED')
  AND available_at <= now()
ORDER BY priority ASC, created_at ASC
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

## 9.5 종료 조건

- 40건 처리
- 내부 경과시간 4분 30초
- GitHub job 5분 제한 접근
- `America/Los_Angeles` 기준 일일 내부 목표 도달
- 브리프 예약량만 남음
- 연속 오류 임계값 도달

---

# 10. 데이터베이스 설계

핵심 테이블:

```text
profiles
sources
source_runs
raw_items
content_clusters
cluster_members
analysis_jobs
analyses
policy_details
published_items
brief_periods
briefs
brief_items
error_reports
app_settings
operation_events
workflow_usage
manual_submissions
```

## 10.1 analyses

```sql
analyses
- id uuid PK
- cluster_id uuid FK
- model_name text
- prompt_version text
- schema_version text
- is_robot_related boolean
- category text
- region text
- robot_field text
- importance text
- evidence_level text
- kiro_relevance text
- display_title text
- one_line_summary text
- verified_facts jsonb
- numbers_and_dates jsonb
- ai_interpretation text
- kiro_implication text
- limitations text
- keywords jsonb
- policy_meta jsonb nullable
- raw_response jsonb nullable
- input_token_count integer nullable
- output_token_count integer nullable
- generated_at timestamptz
- validation_status text
```

## 10.2 policy_details

```sql
policy_details
- published_item_id uuid PK FK
- policy_name text nullable
- project_name text nullable
- ministries text[] nullable
- organizations text[] nullable
- budget_text text nullable
- budget_amount_krw numeric nullable
- project_start_date date nullable
- project_end_date date nullable
- support_targets text[] nullable
- announcement_status text nullable
- application_deadline date nullable
- target_region text nullable
- created_at timestamptz
- updated_at timestamptz
```

## 10.3 published_items

```sql
published_items
- id uuid PK
- cluster_id uuid FK UNIQUE
- current_analysis_id uuid FK
- title text
- category text
- region text
- robot_field text
- importance text
- evidence_level text
- kiro_relevance text
- published_at timestamptz
- source_published_at timestamptz nullable
- representative_source_name text
- representative_url text
- related_source_count integer
- search_text text
- is_visible boolean
- hidden_reason text nullable
- created_at timestamptz
- updated_at timestamptz
```

## 10.4 manual_submissions

```sql
manual_submissions
- id uuid PK
- submitted_by uuid FK
- url text
- priority integer
- status text
- raw_item_id uuid nullable
- error_message text nullable
- created_at timestamptz
- completed_at timestamptz nullable
```

## 10.5 workflow_usage

```sql
workflow_usage
- id uuid PK
- workflow_name text
- workflow_run_id text
- started_at timestamptz
- completed_at timestamptz nullable
- duration_seconds integer nullable
- fetch_duration_seconds integer nullable
- normalize_duration_seconds integer nullable
- cluster_duration_seconds integer nullable
- analysis_duration_seconds integer nullable
- collected_count integer
- analyzed_count integer
- api_call_count integer
- remaining_pending_count integer nullable
- oldest_pending_age_minutes integer nullable
- keepalive_performed boolean default false
- keepalive_status text nullable
- status text
- created_at timestamptz
```

---

# 11. 한국어 검색 설계

Phase 1 검색은 `pg_trgm` 부분 문자열 검색을 기본으로 한다.

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX published_items_search_text_trgm_idx
ON published_items
USING gin (search_text gin_trgm_ops);

CREATE INDEX published_items_title_trgm_idx
ON published_items
USING gin (title gin_trgm_ops);
```

`search_text` 구성:

```text
제목
한 문장 요약
확인된 사실
AI 해석
KIRO 시사점
기관·기업
정책명·사업명
기술명
키워드
```

예상 동작:

```text
저장: 휴머노이드가 제조 현장에 적용됐다
검색: 휴머노이드
결과: 노출
```

데이터량 증가 후 필요하면 PGroonga를 Phase 1.1에서 검토한다.

---

# 12. AI 기사 분석 출력 스키마

```json
{
  "is_robot_related": true,
  "relevance_reason": "로봇 정책 및 실증과 직접 관련",
  "display_title": "간결한 제목",
  "one_line_summary": "한 문장 요약",
  "category": "정책",
  "region": "국내",
  "robot_field": "휴머노이드·피지컬 AI",
  "importance": "높음",
  "evidence_level": "강함",
  "kiro_relevance": "직접",
  "verified_facts": [
    "원문에서 확인된 사실"
  ],
  "numbers_and_dates": [
    {
      "label": "사업기간",
      "value": "2026~2030",
      "source_basis": "원문"
    }
  ],
  "policy_meta": {
    "policy_name": "정책명",
    "project_name": "사업명",
    "ministries": ["산업통상자원부"],
    "organizations": ["전담기관"],
    "budget_text": "총 300억원",
    "budget_amount_krw": 30000000000,
    "project_start_date": "2026-01-01",
    "project_end_date": "2030-12-31",
    "support_targets": ["로봇 기업"],
    "announcement_status": "발표",
    "application_deadline": null,
    "target_region": "전국"
  },
  "ai_interpretation": "변화 방향",
  "kiro_implication": "KIRO 차원의 일반적 시사점",
  "limitations": "확인되지 않은 내용",
  "keywords": ["피지컬 AI", "실증"]
}
```

정책 콘텐츠가 아니면 `policy_meta`는 `null`이다.

---

# 13. AI 결과 검증

- 필수 필드와 enum 검증
- 배열·객체 타입 검증
- 원문에 없는 숫자·날짜 과다 생성 경고
- `verified_facts`가 비어 있으면 게시하지 않음
- 기업 성능 주장은 `기업 발표 기준` 표시
- 금액 숫자 변환 실패 시 원문 텍스트만 보존

게시 조건:

```text
is_robot_related = true
AND validation_status = PASS
AND verified_facts 1개 이상
```

---

# 14. 격주 브리프 설계

## 14.1 대상

```text
importance = 높음 우선
evidence_level = 강함 또는 보통 우선
is_visible = true
```

분야별 최대:

```text
정책 12건
산업 10건
기술 10건
전체 30건
```

## 14.2 3개 섹션 분할 생성

### 호출 1

- 한 페이지 요약
- 핵심 변화 3~5개
- 정책 동향

### 호출 2

- 산업 동향
- 기술 동향

### 호출 3

- 이전 기간 대비 달라진 점
- KIRO 시사점
- 향후 2주 추적 항목

애플리케이션 코드가 최종 문서로 조립한다.

호출 예산:

```text
기본 3회
섹션 재시도 1회
예비 또는 폴백 1회
최대 5회
```

---

# 15. 화면 설계

## 15.1 사용자 메뉴

1. 홈
2. 최신 동향
3. 정책·R&D
4. 격주 브리프
5. 아카이브
6. 통합검색

## 15.2 운영 메뉴

1. 운영 현황
2. 수집원 관리
3. 수동 URL 등록
4. 오류 신고
5. 콘텐츠 숨김·재분석
6. 브리프 관리
7. Gemini·Actions 사용 현황

## 15.3 정책·R&D 화면

표시:

```text
정책·사업명
부처·기관
발표일
사업기간
예산
지원 대상
지역
로봇 분야
발표·공고 상태
```

필터:

```text
부처
기관
기간
지역
로봇 분야
상태
```

## 15.4 운영 현황

```text
마지막 수집 성공
마지막 분석 성공
24시간 이상 미실행 여부
이번 달 Actions 실행시간
1,500분 내부 예산 사용률
오늘 Gemini 호출 수
429 발생 횟수
분석 대기 건수
30분 초과 잠금 복구 건수
장기 실패 수집원
오류 신고
최근 브리프 상태
다음 브리프 일정
최근 백업 상태
마지막 기본 브랜치 활동 시각
마지막 keepalive 성공·실패 상태
```

---

# 16. 인증 설계

- Supabase Auth
- 이메일·비밀번호 또는 매직 링크
- 허용 이메일 도메인 검사
- 이메일 인증 완료 후 활성화

설정 예시:

```text
allowed_email_domains = ["kiro.re.kr"]
```

실제 도메인은 설정값으로 관리한다.

권한:

```text
USER
- 전체 내부 콘텐츠 열람
- 검색
- 오류 신고

OPERATOR
- USER 권한
- 수집원 관리
- 수동 URL 등록
- 숨김·재분석
- 브리프 재생성
- 운영 현황
```

---

# 17. API 설계

## 사용자 API

```text
GET  /api/items
GET  /api/items/:id
GET  /api/search
GET  /api/policies
GET  /api/briefs
GET  /api/briefs/:id
POST /api/error-reports
```

## 운영 API

```text
GET   /api/admin/dashboard
GET   /api/admin/sources
POST  /api/admin/sources
PATCH /api/admin/sources/:id
POST  /api/admin/manual-items
POST  /api/admin/items/:id/reanalyze
POST  /api/admin/items/:id/hide
GET   /api/admin/error-reports
PATCH /api/admin/error-reports/:id
POST  /api/admin/briefs/:periodId/regenerate
```

---

# 18. 백업 설계

## 18.1 백업 대상

```text
sources
content_clusters
cluster_members
analyses
policy_details
published_items
brief_periods
briefs
brief_items
app_settings
```

## 18.2 주기

- 주 1회
- GitHub Actions `backup.yml`
- 실행시간 최대 10분
- 압축 논리 덤프 생성

## 18.3 저장과 보존

- 비공개 GitHub Actions artifact
- 보존기간: 28일
- 보존 개수 목표: 최근 4개
- 압축 형식: gzip
- `raw_text`와 원본 HTML 제외
- API 키·비밀번호 포함 금지

예시:

```yaml
- name: Upload database backup
  uses: actions/upload-artifact@v4
  with:
    name: kiro-db-backup-${{ github.run_id }}
    path: backup.sql.gz
    retention-days: 28
```

다음 항목을 운영 기록에 저장한다.

```text
backup_size_bytes
backup_started_at
backup_completed_at
restore_test_status
```

파일럿 기간 중 최소 1회 복구 테스트를 수행한다.

---

# 19. GitHub Actions 워크플로

## collect-and-analyze.yml

```text
3시간마다
1. 환경 확인
2. 30분 초과 잠금 복구
3. 수집
4. 로컬 필터
5. 클러스터링
6. 큐 처리
7. 사용량 기록
```

`timeout-minutes: 5`

## generate-brief.yml

```text
격주
1. 대상 기간 확정
2. 대상 선정
3. 3개 섹션 생성
4. 코드 조립
5. 자동 발행
6. 사용량 기록
```

`timeout-minutes: 10`

## backup.yml

```text
주 1회
1. 마지막 기본 브랜치 커밋 시각 확인
2. 45일 경과 시 keepalive 마커 커밋
3. 핵심 테이블 덤프
4. 압축
5. retention-days 28로 artifact 업로드
6. 백업·keepalive 상태 기록
```

`timeout-minutes: 10`

## cleanup.yml

```text
주 1회
- 오래된 raw_text 삭제
- 실패 작업 보존기간 정리
- 통계 갱신
```

잠금 복구는 cleanup이 아니라 매 분석 배치 시작 시 수행한다.

## 19.1 수집·분석 job 분리 판단 기준

파일럿에서는 단일 job으로 시작한다. 다음 중 하나가 3일 이상 지속되면 수집 workflow와 분석 workflow 분리를 검토한다.

- 하루 분석량 200건 미만
- 가장 오래된 분석 대기시간 12시간 초과
- 수집·정제 단계가 전체 실행시간의 40% 이상
- 배치가 반복적으로 5분 timeout에 도달
- `remaining_pending_count`가 연속 증가

분리 시에도 월간 Actions 1,500분 내부 예산을 우선 적용한다.

---

# 20. 테스트 설계

## 단위 테스트

- URL 정규화
- 로컬 필터
- 제목 유사도
- 대표 출처 선택
- JSON 스키마
- policy_meta 정규화
- 오래된 잠금 복구
- 재시도 시간 계산
- 한국어 부분검색
- 브리프 섹션 조립

## 통합 테스트

- RSS → raw_items
- 수동 URL → raw_items
- raw_items → cluster
- cluster → queue
- Gemini 응답 → analyses
- policy_meta → policy_details
- analyses → published_items
- 3개 브리프 섹션 → briefs

## 실패 테스트

- RSS 500
- 본문 추출 실패
- Gemini 429
- 잘못된 JSON
- Actions 5분 강제 종료
- PROCESSING 잠금 잔류
- DB 연결 실패
- 브리프 한 섹션 실패
- Supabase 일시 중지 후 재연결
- PT 날짜 전환 전후 일일 호출 카운터
- 45일 미만 keepalive 미실행
- 45일 이상 keepalive 커밋
- branch protection으로 keepalive push 실패
- backup artifact 28일 보존 설정

---

# 21. 구현 단계

## Stage 1 — 기반

- 비공개 GitHub 저장소
- Next.js
- Supabase
- 인증과 도메인 허용
- DB 마이그레이션
- `pg_trgm`

## Stage 2 — 수집

- RSS·목록 수집
- 수동 URL
- 원문 정제
- URL 정규화
- raw_items

## Stage 3 — 필터·클러스터·큐

- 로컬 필터
- 단순 클러스터
- 대표 자료
- analysis_jobs
- 잠금과 잠금 복구
- Actions 5분 제한

## Stage 4 — AI 기사 분석

- GeminiProvider
- 구조화 출력
- policy_meta
- 결과 검증
- policy_details
- published_items
- 호출량 기록

## Stage 5 — 사용자 화면

- 홈
- 최신 동향
- 상세
- 정책·R&D
- `pg_trgm` 검색
- 아카이브

## Stage 6 — 브리프

- 기간 생성
- 대상 선정
- 3개 섹션 생성
- 코드 조립
- 자동 발행
- 버전 보존

## Stage 7 — 운영

- 수집원 관리
- 수동 URL
- 오류 신고
- 숨김·재분석
- Actions·Gemini 현황
- keepalive 상태
- 백업 상태

## Stage 8 — 파일럿

- 초기 수집원 등록
- 2주 이상 자동 운영
- 브리프 최소 1회 발행
- 검색 품질 확인
- Actions 실행시간과 단계별 처리시간 측정
- 처리량 부족 시 job 분리 조건 평가
- 관리시간 측정
- 백업·복구 테스트
- Phase 1 완료 판정

---

# 22. 주요 설계 결정 요약

```text
저장소
- 비공개 GitHub

Actions 예산
- 월 1,500분
- 1,200분 경고
- 수집·분석 job 5분 제한

AI 기사 모델
- Gemini 3.1 Flash Lite

AI 브리프 모델
- Gemini 3 Flash 또는 2.5 Flash

기사 처리
- 대표 콘텐츠당 구조화 호출 1회

브리프 처리
- 3개 섹션 호출 후 코드 조립
- 최대 5회 호출

배치
- 3시간 주기
- 40건 또는 4분 30초 내부 종료

큐
- PostgreSQL
- 30분 초과 PROCESSING 자동 복구

검색
- pg_trgm 기반 한국어 부분검색

정책·R&D
- policy_meta AI 출력
- policy_details 정규화 테이블

DB
- Supabase PostgreSQL

백업
- 주 1회 핵심 테이블 논리 덤프
- artifact 28일 보존, 최근 4개 목표

Keepalive
- 마지막 커밋 45일 경과 시 마커 자동 커밋

Gemini 일일 기준
- `America/Los_Angeles` 날짜 기준

인증
- 허용 이메일 도메인 설정
- USER / OPERATOR

운영
- 사전 승인 없음
- 월 1~2시간 점검

외부 AI 폴백
- Phase 1 제외
```

---

# 23. tasks.md 작성 전 남은 설정값

- 실제 허용 이메일 도메인
- 초기 수집원 목록
- 격주 발행 요일과 시각
- 프론트엔드 무료 호스팅
- 정제 본문 최대 길이
- raw_text 보존기간
- 제목 유사도 임계값
- Flash Lite 일일 내부 목표값
- 브리프 기본 모델
- keepalive 커밋이 허용되도록 기본 브랜치 보호 규칙 조정 여부
- 기존 HWPX 자료 초기 이관 여부
