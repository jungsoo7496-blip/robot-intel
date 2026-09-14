/** 화면 표기용 이름 — 표가 옆으로 넘치지 않게 모델 ID를 짧게 쓴다. */
const MODEL_LABELS: Record<string, string> = {
  "gemini-flash-lite-latest": "3.5 Flash-Lite",
  "gemini-3.1-flash-lite": "3.1 Flash-Lite",
  "gemini-flash-latest": "Flash (브리프)",
};

export function modelLabel(model: string): string {
  return MODEL_LABELS[model] ?? model;
}

/** 실행 메모 속 모델 ID도 같은 짧은 이름으로 바꾼다. */
export function shortenNote(note: string | null): string {
  if (!note) return "—";
  // 긴 ID부터 바꿔야 짧은 ID가 긴 ID의 일부를 먼저 먹지 않는다.
  const ids = Object.keys(MODEL_LABELS).sort((a, b) => b.length - a.length);
  let out = note;
  for (const id of ids) out = out.split(id).join(MODEL_LABELS[id]);
  return out;
}

export const WORKFLOW_LABELS: Record<string, string> = {
  analyze: "분석",
  collect: "뉴스 수집",
  analyze_reports: "보고서 분석",
  "generate-brief": "브리프 생성",
  cleanup: "데이터 정리",
  backup: "백업",
};

export function workflowLabel(name: string): string {
  return WORKFLOW_LABELS[name] ?? name;
}
