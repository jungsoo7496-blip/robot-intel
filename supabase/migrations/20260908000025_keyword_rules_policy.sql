-- keyword_rules RLS 정책을 프로젝트 공통 헬퍼 public.is_operator()로 교체 (2026-09-08).
--
-- 0022의 정책은 profiles.role = 'OPERATOR'만 보고 is_active를 확인하지 않았다.
-- 운영자 권한을 내려도(is_active = false) auth 사용자는 남으므로, 그 계정이 로그인해
-- 얻은 JWT + 브라우저에 공개된 anon 키로 PostgREST에 직접 붙으면 뉴스 필터·보고서
-- 계층·R&D 키워드를 바꿔 수집 결과를 조작할 수 있었다(화면은 service role +
-- requireOperator()라 무관). is_operator()는 role과 is_active를 함께 본다 —
-- sources·app_settings 등 다른 운영 테이블 정책이 이미 쓰는 방식이다.
--
-- 화면은 service role, 배치는 DB URL로 접근하므로 이 정책은 authenticated 경로에만
-- 영향을 준다.

DROP POLICY IF EXISTS keyword_rules_operator ON public.keyword_rules;

CREATE POLICY keyword_rules_operator
ON public.keyword_rules FOR ALL
TO authenticated
USING (public.is_operator())
WITH CHECK (public.is_operator());
