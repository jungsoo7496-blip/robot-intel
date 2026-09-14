# KIRO 로봇 인텔리전스 플랫폼

공개된 국내외 로봇 정책·산업·기술 정보를 자동으로 수집하고, Gemini API(무료 티어)로
분류·요약·분석하여 KIRO 내부 직원에게 제공하는 내부 웹사이트입니다.

- 기준 요구사항: `docs/KIRO_robot_intelligence_requirements_v0.3.md`
- 기준 설계: `docs/KIRO_robot_intelligence_design_v0.3.md`
- 작업 목록: `docs/KIRO_robot_intelligence_tasks_v0.2.md`
- 대상 단계: 완전 무료 프로토타입 / Phase 1 MVP

## 구성

```text
/
├─ src/                  # Next.js 앱 (App Router)
│  ├─ app/               # 라우트
│  ├─ components/        # UI 컴포넌트
│  ├─ lib/               # Supabase 클라이언트, 유틸
│  └─ types/             # 공용 타입·스키마
├─ scripts/
│  └─ kiro_batch/        # Python 수집·분석 배치
├─ prompts/              # Gemini 프롬프트 (버전 관리)
├─ supabase/
│  ├─ migrations/        # DB 마이그레이션 SQL
│  └─ seed.sql           # 초기 데이터
├─ tests/                # 단위·통합 테스트
├─ .github/workflows/    # 수집·브리프·백업·정리 스케줄
└─ docs/                 # 사양 문서
```

## 로컬 실행

### 1. 프론트엔드 (Next.js)

```bash
npm install
cp .env.example .env.local   # 값 채우기
npm run dev
```

http://localhost:3000 에서 확인합니다.

### 2. Python 배치

```bash
cd scripts
python -m venv .venv
.venv\Scripts\activate       # Windows
pip install -r requirements.txt
python -m kiro_batch.collect          # 수집 1회 실행
python -m kiro_batch.analyze          # 분석 큐 처리 1회 실행
```

### 3. 데이터베이스

Supabase 프로젝트를 생성한 뒤 `supabase/migrations/`의 SQL을 순서대로 적용합니다.

```bash
# Supabase CLI 사용 시
supabase db push
```

## 환경변수

`.env.example`를 참고하세요. 비밀값은 절대 커밋하지 않습니다.
GitHub Actions에서 사용하는 값은 저장소 Secrets로 관리합니다.

| 변수 | 용도 |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase 프로젝트 URL (브라우저 노출 가능) |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon 키 (브라우저 노출 가능) |
| `SUPABASE_SERVICE_ROLE_KEY` | 서비스 역할 키 — 서버·배치 전용, 브라우저 금지 |
| `SUPABASE_DB_URL` | Python 배치·백업용 PostgreSQL 직접 연결 문자열 |
| `GEMINI_API_KEY` | Gemini API 키 — 배치 전용 |
| `ALLOWED_EMAIL_DOMAINS` | 가입 허용 이메일 도메인 (쉼표 구분) |

## 운영 원칙 (요약)

- 무료 티어 우선: Supabase Free, GitHub Actions, Gemini Free Tier
- 배치 우선: 3시간 주기 수집, 실패는 지연으로 처리 (429 = 이월)
- Actions 월간 내부 예산 1,500분, 수집·분석 job은 `timeout-minutes: 5`
- Gemini 일일 쿼터는 `America/Los_Angeles` 날짜 기준으로 집계
- 30분 초과 `PROCESSING` 잠금은 매 분석 배치 시작 시 자동 복구
- 공개자료만 AI에 입력, 내부 문서 입력 금지
