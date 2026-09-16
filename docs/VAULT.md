# 파일 금고(vault) 운영 안내

운영자(비개발자)용. 금고가 무엇이고, 어디에 무엇이 쌓이고, 매일·매주 무엇이 돌고,
어디를 보면 되고, 문제가 나면 무엇을 하면 되는지를 적었다. 코드 설계는
`scripts/kiro_batch/vault.py` 첫머리 주석, 미러는 옛 저장소의 `scripts/vault_mirror.py`.

## 1. 금고란

- Supabase 무료 DB(500 MB)가 차지 않도록, **오래된 카드의 무거운 부속**(AI 분석 전체 이력·
  관련 출처 목록·기사 본문·정책 상세)을 **파일로 옮겨 두는 곳**이다.
- 카드 자체(제목·날짜·업무축 등 목록에 보이는 것)는 DB에 그대로 남고, **기사는 하나도
  지워지지 않는다.** 상세 화면은 금고 파일에서 부속을 읽어 예전과 같이 보여준다.
- 사본이 **두 곳**(Supabase Storage + GitHub Release)에 다 있고 **검증이 끝난 기간만**
  DB에서 지운다. 지우는 단계(prune)는 스위치(`vault_prune_enabled`)가 켜져 있을 때만 돈다.

## 2. 어디에 무엇이 저장되나

**기간 단위 = 반달.** `YYYY-MM-a` = 그 달 1~15일, `YYYY-MM-b` = 16일~말일 (한국 시간 기준,
카드의 발행일로 나눈다). 한 달에 파일 하나로 두면 Storage 무료 파일 상한(50 MB)을 두세 달
안에 넘기기 때문에 둘로 나눴다. 오늘이 속한 기간은 절대 대상이 아니다.

| 어디 | 무엇 | 비고 |
| --- | --- | --- |
| Supabase Storage 비공개 버킷 `vault` | `items/YYYY-MM-a.jsonl.gz` — 그 기간 카드 전부, 카드 1건 = 압축 덩어리 1개<br>`items/YYYY-MM-a.idx.json` — 카드 id → 파일 안 위치 | **화면이 읽는 사본.** 상세 화면은 필요한 카드 1건만 잘라서 받는다 |
| GitHub Release (옛 비공개 저장소 `robotreport`) | 태그 `vault-YYYY-MM-a`, 같은 두 파일 | **두 번째 사본.** 매주 미러가 만든다. Storage 사본이 망가지면 여기서 되살린다 |
| DB 표 `vault_manifest` | 기간별 대장 — 한 기간이 한 행 | 어느 단계까지 끝났는지 시각으로 남는다 (아래 3절) |
| DB `published_items.vaulted_at` | 카드의 부속이 DB에서 지워진 시각 | 값이 있으면 화면은 금고에서 읽고, 재분석이 막힌다 |
| DB `published_items.kiro_axes` | 카드의 업무축 (분석에서 복사) | 부속이 지워져도 업무축 필터에서 카드가 사라지지 않게 |

금고 파일의 카드 1건에는: 카드 기본 정보(id·발행일·제목·표시 여부), **현재 분석**(`analysis`),
**이전 분석 전부**(`analysis_history`), 관련 출처 목록, 대표 기사 본문, 정책 상세가 들어간다.
즉 그 카드에 대해 AI가 만든 분석은 하나도 빠지지 않고 파일에 있다.

`vault_manifest` 열 읽는 법:

| 열 | 뜻 |
| --- | --- |
| `month` | 기간 문자열 (`2026-06-a`). 열 이름은 옛 설계 그대로다 |
| `rows` / `bytes` / `sha256` | 파일에 든 카드 수 / 파일 크기 / 파일 지문. 검증·미러가 이 값과 대조한다 |
| `exported_at` | 파일을 만들어 Storage에 올린 시각 |
| `storage_verified_at` | 올린 파일을 다시 내려받아 대조해 통과한 시각 |
| `release_verified_at` / `release_tag` | Release에 두 번째 사본을 올리고 확인한 시각 / 태그 |
| `pruned_at` / `pruned_rows` | DB에서 부속을 지우기 끝낸 시각 / 지운 카드 수 |
| `verify_failures` | 검증 연속 실패 횟수. 3이 되면 자동 재시도를 멈춘다 (5절) |
| `analysis_ids` | 카드별로 파일에 들어간 분석 id 목록. prune은 **여기 적힌 분석만** 지운다 |

정상이면 한 행의 시각이 `exported_at → storage_verified_at → release_verified_at → pruned_at`
순서로 채워진다. 어디까지 채워졌는지가 곧 그 기간의 상태다:

- `exported_at`만 있음 → 검증 대기 (같은 실행에서 바로 검증하므로, 이 상태로 남아 있으면 검증 실패)
- `storage_verified_at`까지 → 미러 대기 (다음 월요일 새벽)
- `release_verified_at`까지 → 정리 대기 (스위치가 켜져 있으면 다음 날 새벽)
- `pruned_at`까지 → 완료

## 3. 매일·매주 무엇이 도나

```
[매일 03:47 KST]  공개 저장소 robot-intel · cleanup 워크플로 (기존 일일 정리 뒤에 이어서)
   ① 내보내기  기간 마지막 날 + vault_window_days 가 지난 기간 중 아직 검증 안 된 것을
              파일로 만들어 Storage 에 올린다 (실행당 최대 3개 기간)
   ② 검증      바로 다시 내려받아 지문·크기·카드 수를 대조하고, 무작위 20건을 화면과
              같은 방식으로 잘라 받아 DB 와 비교 → 통과하면 storage_verified_at
   ③ 정리      vault_prune_enabled = true 이고 storage_verified_at·release_verified_at 이
              둘 다 있는 기간만: 대장에 적힌 분석·끝난 분석 작업을 지우고, 본문을 비우고,
              카드에 vaulted_at 을 찍는다 (200건씩, 실행당 vault_prune_max_per_run 건까지)
   ④ 자가점검  대장 ↔ 실제 카드 수 ↔ Storage 파일 존재 ↔ 미러 지연을 대조

[매주 월요일 03:37 KST]  옛 저장소 robotreport · backup 워크플로
   DB 덤프(기존) + vault-mirror 작업: storage_verified_at 있고 release_verified_at 없는
   기간을 Storage 에서 내려받아 대장과 대조 → Release(vault-YYYY-MM-a) 에 올림 →
   올라간 크기 재확인 → release_verified_at 기록
```

예: `2026-06-a`(6월 1~15일)는 기본 창(14일) 기준 6월 말 새벽 실행에서 내보내기·검증되고,
그다음 월요일에 미러되고, 스위치가 켜져 있으면 그다음 날 정리된다. 내보내기·검증·미러는
스위치와 무관하게 항상 돈다.

정리(③)에서 **지우는 것**: 대장 `analysis_ids`에 적힌 분석 행, 끝난 분석 작업(analysis_jobs),
기사 본문(다른 미정리 카드의 대표 기사인 본문은 남긴다). **지우지 않는 것**: 카드, 클러스터,
클러스터 구성원 목록(cluster_members), 기사 행 자체(같은 기사가 다시 수집되는 것을 막는다).

## 4. 어디를 보면 되나

| 볼 곳 | 무엇을 |
| --- | --- |
| 관리 화면 → **데이터 관리** (`/admin/data`) | DB 용량 추세와 보존 설정. 금고 설정 항목이 화면에 있으면 여기서 고친다 |
| Supabase 대시보드 → Table Editor → `vault_manifest` | 기간별 진행 상태 (2절 표). 시각이 순서대로 채워지고 `verify_failures`가 0이면 정상 |
| Supabase 대시보드 → Table Editor → `operation_events` | `event_type`이 `VAULT_`로 시작하는 행 — 문제가 있을 때만 생긴다. 없으면 정상 |
| GitHub → robot-intel → Actions → **cleanup** | 실행 로그에서 `[vault]`로 시작하는 줄 |
| GitHub → robotreport(옛) → Actions → **backup** → `vault-mirror` | 미러 결과. 실패하면 작업이 빨간색 |

한 번에 보는 SQL (Supabase 대시보드 → SQL Editor 에 붙여 넣기):

```sql
-- 기간별 진행 상태
SELECT month, rows, round(bytes / 1048576.0, 1) AS mb, exported_at, storage_verified_at,
       release_verified_at, pruned_at, pruned_rows, verify_failures
FROM vault_manifest ORDER BY month;

-- 최근 금고 이벤트
SELECT created_at, event_type, reason
FROM operation_events
WHERE event_type LIKE 'VAULT_%'
ORDER BY created_at DESC LIMIT 50;
```

이벤트 뜻과 조치:

| event_type | 뜻 | 조치 |
| --- | --- | --- |
| `VAULT_VERIFY_FAILED` | 내보낸 파일을 다시 내려받아 대조했는데 어긋났다. 그 기간은 `storage_verified_at`이 비고 `verify_failures`가 1 오른다. **다음 날 자동으로 다시 내보내고 검증한다** | 1~2번이면 지켜본다(일시적 네트워크 문제가 대부분). `reason`에 무엇이 어긋났는지 적혀 있다 |
| `VAULT_VERIFY_STALLED` | 같은 기간이 **3번 연속** 검증에 실패해 자동 재시도를 멈췄다 (한 번만 기록된다) | 원인을 본다: cleanup 실행 로그의 `[vault]` 줄, Supabase Storage 상태, 비밀값(7절). 고친 뒤 **5-1**로 재개 |
| `VAULT_MIRROR_STALE` | Storage 검증은 끝났는데 **14일이 지나도** Release 미러가 안 됐다 | 옛 저장소 backup 워크플로의 최근 실행을 본다 — 실패했으면 로그 확인(비밀값 2개가 있는지), 안 돌았으면 **5-3**으로 수동 실행 |
| `VAULT_MISMATCH` | 대장과 실제가 어긋난다. `reason`·`detail`에 어떤 종류인지 적힌다:<br>(a) **재분석된 카드** — 내보낸 뒤 그 카드가 다시 분석돼 현재 분석이 파일에 없다. 그 카드는 건드리지 않고 통째로 건너뛴다<br>(b) 그 기간 DB 카드 수 ≠ 대장 `rows`<br>(c) 검증된 파일이 없는데 비워진 카드가 있다<br>(d) Storage에 파일이 없다 | (a) 조치 없음 — 카드는 DB에 그대로 있어 화면도 정상이다. 해당 기간은 '정리 미완'으로 남는다<br>(b) 정리 전이면 **5-2**로 다시 내보낸다(다시 만든 파일이 새 카드까지 담는다)<br>(c)·(d) 화면에서 그 기간 카드 상세가 안 열릴 수 있다. Release 사본이 있으면 Storage에 같은 파일을 다시 올리고(경로·이름 동일) 자가점검 결과를 본다. 확실치 않으면 개발자와 상의 |

## 5. 복구 절차

아래 SQL은 Supabase 대시보드 → SQL Editor 에서 실행한다. `'2026-06-a'` 자리에 해당 기간을 넣는다.

### 5-1. 검증 실패 반복(`VAULT_VERIFY_STALLED`) 뒤 재개

원인을 고친 뒤 실패 횟수를 0으로 되돌리면 다음 날 새벽 실행이 다시 내보내고 검증한다.

```sql
UPDATE vault_manifest SET verify_failures = 0 WHERE month = '2026-06-a';
```

### 5-2. 다시 내보내기 (파일을 새로 만들기)

**조건: 그 기간에 이미 비워진(vaulted_at 있는) 카드가 0건일 때만.** 비워진 카드가 있으면
DB에 분석이 없어 새 파일에는 빈 분석이 들어가고, 이걸로 Storage 파일을 덮으면 분석을
영구히 잃는다. 배치도 이 경우 스스로 거부한다.

```sql
-- 1) 비워진 카드 수 확인 — 0 이어야 한다
SELECT count(*) AS vaulted
FROM published_items
WHERE vaulted_at IS NOT NULL
  AND to_char(published_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM')
      || CASE WHEN extract(day FROM published_at AT TIME ZONE 'Asia/Seoul') <= 15
              THEN '-a' ELSE '-b' END
      = '2026-06-a';

-- 2) 0 이면: Storage 검증 표식을 지운다 → 다음 날 새벽 실행이 파일을 새로 만들어 올리고 검증한다
UPDATE vault_manifest SET storage_verified_at = NULL WHERE month = '2026-06-a';
```

새로 내보내면 `sha256`이 바뀌고 `release_verified_at`도 자동으로 비워져, 다음 월요일 미러가
Release 자산을 새 파일로 덮어쓴다(같은 태그, 같은 파일 이름).

### 5-3. 미러 다시 돌리기

GitHub → 옛 저장소 `robotreport` → Actions → **backup** → Run workflow. 미러는
`storage_verified_at`이 있고 `release_verified_at`이 없는 기간만 골라 다시 올린다
(이미 올라간 자산은 덮어쓴다). 로그에 `vault_manifest 갱신 0행`이 나오면, 미러하는 사이
그 기간 파일이 다시 내보내진 것이다 — 다음 날 검증이 끝난 뒤 한 번 더 돌리면 된다.

### 5-4. 다른 기간에 영향 없음

모든 단계는 기간 하나씩 따로 처리한다. 한 기간이 실패해도 다른 기간은 정상 진행되며,
실패한 기간은 다음 실행에서 자동으로 다시 시도된다(검증 3회 실패만 예외 — 5-1).

## 6. 금고 카드에서 안 되는 것

`vaulted_at`이 찍힌 카드(부속이 DB에서 지워진 카드)는:

- **재분석 요청이 안 된다.** 분석에 쓸 본문이 DB에 없다. 관리자 버튼은 숨겨지고, 요청이
  들어와도 거부된다. 다시 분석해야 할 사정이 있으면 개발자와 상의(금고 파일에서 본문을
  꺼내 와야 한다).
- **그 기간의 주간 브리프 재생성이 안 된다.** 브리프 배치는 DB의 분석을 읽는데 금고
  카드의 분석은 DB에 없다. 브리프 재생성은 아직 금고에 들어가지 않은 최근 주차만 가능하다
  (기본 창 14일이면 대략 최근 2~4주).

되는 것: 목록·검색·업무축 필터(카드의 `kiro_axes`로 필터한다), 상세 화면 열람(금고에서
읽는다 — Storage 장애 때는 카드 기본 정보만 보이고 안내 문구가 나온다), 숨기기·보이기.

## 7. 필요한 비밀값 (이름만)

값은 절대 문서·채팅에 적지 않는다. 등록·확인은 GitHub → 저장소 → Settings → Secrets and
variables → Actions (이름만 보인다).

**공개 저장소 `robot-intel` — 14개** (`scripts/set_public_secrets.bat`이 `.env`에서 골라 올린다):
`SUPABASE_DB_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`,
`NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `DATA_GO_KR_API_KEY`, `NANET_DETAIL_API_KEY`,
`NKIS_API_KEY`, `PRISM_API_KEY`, `SCIENCEON_AUTH_KEY`, `SCIENCEON_CLIENT_ID`,
`SCIENCEON_MAC_ADDRESS`, `REPORT_RELAY_TOKEN`.
이 중 금고(cleanup)가 쓰는 것은 앞의 3개다.

**옛 저장소 `robotreport` — 금고 미러용 2개 추가** (`scripts/set_archive_secrets.bat`이 올린다):
`NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (백업용 `SUPABASE_DB_URL`은 이미 있다. Release 업로드용 토큰은
GitHub가 자동으로 준다). 이 2개가 없으면 vault-mirror 작업이 종료코드 2로 끝나고 로그에
빠진 이름이 나온다.

`SUPABASE_SERVICE_ROLE_KEY`는 옛 형식(긴 `eyJ…`)이든 새 형식(`sb_secret_…`)이든 된다 —
배치·화면·미러 세 곳이 같은 규칙으로 구분한다. 키를 새 형식으로 바꾸면 세 곳(공개 저장소
Secrets, Vercel 환경변수, 옛 저장소 Secrets)을 같이 바꾼다.

## 8. 설정값 (`app_settings`)

데이터 관리 화면에 항목이 있으면 거기서, 없으면 Supabase → Table Editor → `app_settings`에서
`value`를 고친다(값은 JSON — 숫자는 `14`, 스위치는 `true`/`false`).

| key | 기본 | 뜻 |
| --- | --- | --- |
| `vault_window_days` | `14` | 기간 마지막 날에서 며칠 지나야 그 기간을 내보내는지. **하한 14** — 더 작게 넣으면 배치가 14로 올려 쓰고 로그에 경고한다. 같은 사건 기사를 묶는 병합 창(7일)의 두 배 — 기간이 끝난 뒤에도 얼마간 카드가 바뀔 수 있어서다. 오늘이 속한 기간은 값과 무관하게 절대 대상이 아니다 |
| `vault_prune_enabled` | `false` | DB에서 부속을 지울지. **꺼져 있어도 내보내기·검증·미러·자가점검은 매일/매주 돈다** — 켜기 전까지는 '사본만 만들어 두는' 상태 |
| `vault_prune_max_per_run` | `3000` | 정리 1회 실행에서 처리할 최대 카드 수. 넘는 분량은 다음 날 이어서 한다 |

### `vault_prune_enabled`를 켜는 시점

DB 용량이 여유 있으면 서둘러 켤 필요가 없다(데이터 관리 화면의 용량 추세를 본다).
켜기 전에 다음을 확인한다:

1. 마이그레이션 `supabase/migrations/20260917000001_vault.sql`이 적용돼 있다
   (`vault_manifest` 표가 있고 `app_settings`에 위 세 키가 있다).
2. 비밀값이 두 저장소에 다 있다(7절).
3. `vault_manifest`에 `storage_verified_at`과 `release_verified_at`이 **둘 다** 채워진 기간이
   적어도 하나 있고, 그 기간의 `verify_failures`가 0이다.
4. 최근 `operation_events`에 `VAULT_` 이벤트가 없다(자가점검 이상 없음).
5. 옛 저장소 Releases 목록에 `vault-YYYY-MM-a` 태그와 두 파일이 보인다.

켠 뒤 첫 실행(다음 날 새벽) 후에는: cleanup 로그의 `[vault] … 정리 완료` 줄, `vault_manifest`의
`pruned_at`, 그리고 **그 기간 카드 몇 건의 상세 화면**(분석·관련 출처가 예전처럼 보이는지)을
확인한다. 이상하면 즉시 `false`로 되돌린다 — 이미 정리된 기간은 금고에서 읽으므로 화면은
계속 동작하고, 아직 안 정리된 기간은 그대로 멈춘다.
