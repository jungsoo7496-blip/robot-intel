# 백업 복구 절차

backup.yml이 매주 업로드하는 `backup.tar.gz`에는 다음이 들어 있다.

- `sources_data.sql` — 수집원·앱 설정과, 운영자가 화면에서 고치는 수집 대상 설정
  (검색어 `search_keywords`, 뉴스 검색 대상 `news_search_targets`,
  키워드 규칙 `keyword_rules`). raw_items가 참조하므로 먼저 복구한다.
- `raw_items_meta.csv` — 원문 메타데이터 (본문 raw_text·clean_text 제외)
- `core_data.sql` — 클러스터·분석·게시물·브리프·R&D 공고 (data-only, FK 의존 순서로 덤프)
- `reports_data.sql` — 정책·동향 보고서 4층 (sources/channels/occurrences/documents/analyses/jobs)

백업하지 않는 것:

- `manual_submissions` — raw_items와 순환 FK·profiles 의존 관계 (운영 로그 성격).
  raw_items의 manual_submission_id도 NULL로 백업된다.
- `profiles` — auth.users에 종속.
- 실행 기록 `source_runs` · `report_source_runs` · `report_channel_keyword_runs`.
  마지막은 (채널 × 검색어) 회전 상태라 비어 있으면 모든 조합이 "실행할 차례"가
  되어 하루 이틀 안에 스스로 다시 채워진다.

## 복구 순서 (빈 Supabase 프로젝트 기준)

```bash
# 0) 새 프로젝트에 스키마 먼저 적용 (마이그레이션 순서대로)
#    --single-transaction: 파일 하나가 중간에 실패하면 그 파일 전체가 되돌아간다
#    (마이그레이션 파일은 자기 BEGIN/COMMIT을 쓰지 않는다)
for f in supabase/migrations/*.sql; do
  psql "$NEW_DB_URL" -v ON_ERROR_STOP=1 --single-transaction -f "$f"
done

# 1) 백업 풀기
tar xzf backup.tar.gz

# 2) 마이그레이션이 심어 둔 기본값 비우기 — 백업에 그 표가 들어 있을 때만.
#    app_settings·keyword_rules·search_keywords·news_search_targets는
#    마이그레이션이 기본값을 먼저 넣기 때문에, 비우지 않으면 3)의 COPY가
#    "중복 키" 오류로 멈춘다. 백업에 없는 표는 건드리지 않는다(기본값 유지).
for t in app_settings keyword_rules search_keywords news_search_targets; do
  if grep -q "^COPY public.$t " sources_data.sql; then
    psql "$NEW_DB_URL" -v ON_ERROR_STOP=1 -c "DELETE FROM public.$t;"
  fi
done

# 3) 수집원·설정·검색어
psql "$NEW_DB_URL" -v ON_ERROR_STOP=1 -f sources_data.sql

# 4) raw_items 메타 복구
psql "$NEW_DB_URL" -c "\copy raw_items (
  id, source_id, manual_submission_id, url, canonical_url,
  title, author, feed_summary, published_at, fetched_at,
  language, content_hash, extract_status,
  filter_status, filter_reason, created_at, updated_at
) FROM 'raw_items_meta.csv' WITH (FORMAT csv, HEADER)"

# 5) 나머지 핵심 데이터
psql "$NEW_DB_URL" -v ON_ERROR_STOP=1 -f core_data.sql

# 6) 보고서 데이터 — report_documents ↔ report_occurrences가 순환 FK라
#    FK 검사를 세션에서 끄고 로드한다
psql "$NEW_DB_URL" -v ON_ERROR_STOP=1 \
  -c "SET session_replication_role = replica;" \
  -f reports_data.sql
```

### 7) 검색 키워드 통합(마이그레이션 26) **이전** 백업이면 한 번 더

2026-09-08의 검색 키워드 통합 전에 뜬 백업에는 `search_keywords`·
`news_search_targets`가 없고, `sources`·`report_source_channels`에도 새 컬럼
(`managed_by`·`search_target`·`keyword_term`·`uses_keywords`·`retired_at`)이
없다. 데이터만 되돌리면 새 컬럼은 **기본값**으로 들어와 조용히 어긋난다.

```bash
# 이 값이 0이면 "이전 백업"이다 (1이면 아래를 실행하지 않는다)
grep -c "^COPY public.search_keywords " sources_data.sql

# 이전 백업일 때만 — 마이그레이션 26의 표시 붙이기를 다시 실행한다
psql "$NEW_DB_URL" -v ON_ERROR_STOP=1 --single-transaction \
  -f supabase/migrations/20260908000026_search_keywords.sql
```

이 한 줄이 하는 일과, 빼먹으면 생기는 일:

| 하는 일 | 빼먹으면 |
|---|---|
| 되살아난 네이버 5·구글 4행에 "검색 키워드가 소유" 표시를 붙인다 | 배치가 같은 검색어로 행을 새로 만든다 — 네이버는 이름이 같은 중복 행이 생기고, 구글은 주소가 기존 행과 똑같아 수집 로그에 한 줄만 남기고 건너뛰어져 **검색 키워드 화면이 구글에 아무 효과가 없다** |
| 보고서 채널 24개 중 8개만 템플릿으로 남기고 16개를 은퇴시킨다 | 24개가 모두 살아 있고 검색어 7개가 곱해져 **168조합**이 돌아간다 (정상은 8개 템플릿 기준 50조합) |

주의:

- 이 파일은 여러 번 실행해도 안전하다(전부 `IF NOT EXISTS` /
  `ON CONFLICT DO NOTHING` / 옛 키·새 키 둘 다 매칭). 다만 운영자가 지웠던
  기본 검색어 9개는 다시 생긴다 — 필요 없으면 화면에서 지우면 된다.
- 통합 **이후** 백업에 실수로 실행하면 검증에서 "관리행 태깅 실패"로 멈추고
  `--single-transaction` 덕분에 아무것도 바뀌지 않는다. 그대로 두면 된다.
- 되살아난 수집원 개수가 예전과 다르면(네이버 5·구글 4가 아니면) 같은 검증에서
  멈춘다. 그때는 `sources` 표를 열어 어떤 행이 검색용인지 확인하고
  `managed_by`·`search_target`·`keyword_term`을 직접 채운다.

## 주의

- 본문(raw_text·clean_text)은 백업하지 않는다 — 저작권·용량 정책 (NFR-005).
  복구 후 원문이 필요하면 수집기가 다시 가져온다.
- 마스터 계정은 복구 후 관리 API로 다시 생성하고 OPERATOR로 승격한다.
- 복구할 때 `supabase/seed.sql`·`supabase/seed_sources.sql`은 실행하지 않는다.
  백업의 `sources_data.sql`이 그 내용을 이미 담고 있고, 시드를 겹쳐 넣으면
  `app_settings` 키가 충돌한다. (네이버·구글 검색 수집원은 시드에 없다 —
  검색어 표를 보고 수집 배치가 만든다.)
- 복구 직후 첫 수집 배치(한국시각 06:20)까지는 검색어로 만들어지는 수집원이
  화면에 안 보일 수 있다. 배치가 한 번 돌면 맞춰진다.
- 파일럿 기간 중 최소 1회 이 절차를 임시 프로젝트에 리허설한다 (tasks §16.3).
