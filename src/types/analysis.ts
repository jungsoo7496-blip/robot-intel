/**
 * AI 기사 분석 스키마 — Python 쪽과 동일하게 유지한다 (tasks §8.3).
 * Python: scripts/kiro_batch/schemas.py
 * 변경 시 SCHEMA_VERSION을 함께 올린다.
 */

export const SCHEMA_VERSION = "1.1";
export const KIRO_CONTEXT_VERSION = "kiro_public_context_v1";

/** KIRO 업무축 (브로슈어 공개 기능 기준 8종 — Python KIRO_AXES와 동일 유지) */
export const KIRO_AXES = [
  "정책·전략",
  "R&D 기획",
  "연구개발",
  "실증·시험평가",
  "기업지원·사업화",
  "인재양성",
  "협력·생태계",
  "전략방향",
] as const;
export type KiroAxis = (typeof KIRO_AXES)[number];

export const CATEGORIES = ["정책", "산업", "기술"] as const;
export const REGIONS = ["국내", "미국", "중국", "일본", "유럽", "기타"] as const;
/** 서비스/물류 분리 (0012). '서비스·물류 로봇'은 v1 legacy 데이터에만 존재. */
export const ROBOT_FIELDS = [
  "휴머노이드·피지컬 AI",
  "제조·산업용 로봇",
  "서비스 로봇",
  "물류 로봇",
  "의료·돌봄 로봇",
  "농업 로봇",
  "국방·재난 로봇",
  "해양·특수환경 로봇",
  "핵심 부품·소프트웨어",
  "기타",
] as const;

/** 화면용 축약 라벨 — 칩·배지가 옆으로 늘어지지 않게 (사용자 피드백). */
export const ROBOT_FIELD_SHORT: Record<string, string> = {
  "휴머노이드·피지컬 AI": "휴머노이드·AI",
  "제조·산업용 로봇": "제조·산업",
  "서비스 로봇": "서비스",
  "물류 로봇": "물류",
  "서비스·물류 로봇": "서비스·물류",
  "의료·돌봄 로봇": "의료·돌봄",
  "농업 로봇": "농업",
  "국방·재난 로봇": "국방·재난",
  "해양·특수환경 로봇": "해양·특수",
  "핵심 부품·소프트웨어": "부품·SW",
  기타: "기타",
};

export function shortRobotField(field: string): string {
  return ROBOT_FIELD_SHORT[field] ?? field;
}
export const IMPORTANCE = ["높음", "보통", "낮음"] as const;
export const EVIDENCE_LEVELS = ["강함", "보통", "약함"] as const;
export const KIRO_RELEVANCE = ["직접", "간접", "낮음"] as const;

export type Category = (typeof CATEGORIES)[number];
export type Region = (typeof REGIONS)[number];
export type RobotField = (typeof ROBOT_FIELDS)[number];
/** DB에 남아 있는 과거 값 포함 (0012 분리 전 통합값 — 외부 리뷰 P3) */
export type PersistedRobotField = RobotField | "서비스·물류 로봇";
export type Importance = (typeof IMPORTANCE)[number];
export type EvidenceLevel = (typeof EVIDENCE_LEVELS)[number];
export type KiroRelevance = (typeof KIRO_RELEVANCE)[number];

export type NumberOrDate = {
  label: string;
  value: string;
  source_basis: string;
};

export type PolicyMeta = {
  policy_name: string | null;
  project_name: string | null;
  ministries: string[] | null;
  organizations: string[] | null;
  budget_text: string | null;
  budget_amount_krw: number | null;
  project_start_date: string | null;
  project_end_date: string | null;
  support_targets: string[] | null;
  announcement_status: string | null;
  application_deadline: string | null;
  target_region: string | null;
};

export type ArticleAnalysis = {
  is_robot_related: boolean;
  relevance_reason: string;
  display_title: string;
  one_line_summary: string;
  category: Category | null;
  region: Region | null;
  robot_field: RobotField | null;
  importance: Importance | null;
  evidence_level: EvidenceLevel | null;
  kiro_relevance: KiroRelevance | null;
  /** v1.1: KIRO 관련성 구조화 */
  kiro_relevance_axes: KiroAxis[];
  kiro_relevance_reason: string;
  kiro_watchpoints: string[];
  verified_facts: string[];
  numbers_and_dates: NumberOrDate[];
  policy_meta: PolicyMeta | null;
  ai_interpretation: string;
  kiro_implication: string;
  limitations: string;
  keywords: string[];
};

/** published_items 행 (사용자 화면 표시용 최소 타입) */
export type PublishedItem = {
  id: string;
  cluster_id: string;
  title: string;
  category: Category;
  region: Region;
  robot_field: PersistedRobotField;
  importance: Importance;
  evidence_level: EvidenceLevel;
  kiro_relevance: KiroRelevance;
  published_at: string;
  source_published_at: string | null;
  /** 정렬·표시 기준 날짜 = coalesce(원문 발행일, 게시 시각) — DB 생성 컬럼 (0014) */
  display_date: string;
  representative_source_name: string | null;
  representative_url: string;
  related_source_count: number;
  one_line_summary: string | null;
  kiro_implication_excerpt: string | null;
};
