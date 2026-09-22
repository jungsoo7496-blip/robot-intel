# robot-intel

국내외에 공개된 로봇 정책·산업·기술 소식을 자동으로 모아 AI(Gemini API)로 분류·요약하고,
웹사이트로 보여 주는 도구입니다. 한 사람이 운영하는 내부용 프로젝트이며 외부 협업은 받지 않습니다.
저장소를 공개로 둔 이유는 GitHub Actions 예약 실행을 쓰기 위한 것이고, 코드 재사용 라이선스는
따로 부여하지 않습니다.

## 구성

```text
/
├─ src/                  # Next.js 앱 (App Router)
├─ scripts/              # Python 수집·분석·정리 배치
├─ prompts/              # Gemini 프롬프트 (버전 관리)
├─ supabase/migrations/  # DB 마이그레이션 SQL
├─ tests/                # 단위·통합 테스트
├─ .github/workflows/    # 수집·분석·브리프·정리 예약 실행
└─ docs/                 # 운영 문서
```

## 로컬 실행

```bash
npm install
cp .env.example .env.local   # 값 채우기
npm run dev
```

Python 배치는 `scripts/` 아래에서 가상환경을 만들고 `requirements.txt`를 설치한 뒤 모듈 단위로 실행합니다.
데이터베이스는 Supabase 프로젝트를 만든 뒤 `supabase/migrations/`의 SQL을 순서대로 적용합니다.

## 환경변수

`.env.example`를 참고하세요. 비밀값은 커밋하지 않고, GitHub Actions에서 쓰는 값은 저장소 Secrets로 관리합니다.

| 변수 | 용도 |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase 프로젝트 URL (브라우저 노출 가능) |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon 키 (브라우저 노출 가능) |
| `SUPABASE_SERVICE_ROLE_KEY` | 서비스 역할 키 — 서버·배치 전용 |
| `SUPABASE_DB_URL` | Python 배치용 PostgreSQL 직접 연결 문자열 |
| `GEMINI_API_KEY` | Gemini API 키 — 배치 전용 |
| `ALLOWED_EMAIL_DOMAINS` | 가입 허용 이메일 도메인 (쉼표 구분) |

## 원칙

- 무료 티어 안에서 운영하고, 한도에 걸리면 실패가 아니라 다음 실행으로 이월합니다.
- 공개된 자료만 AI에 입력합니다.
