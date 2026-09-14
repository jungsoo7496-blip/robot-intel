import "server-only";

// ------------------------------------------------------------
// GitHub Actions workflow_dispatch 호출 헬퍼.
//
// 이 두 함수는 원래 admin/actions.ts("use server")에 있었는데, 여러 관리
// 화면에서 재사용하려고 export하는 순간 requireOperator를 거치지 않는
// 서버 액션 엔드포인트가 새로 생긴다("use server" 파일의 export는 다른
// 곳에서 import되지 않아도 액션 ID로 직접 POST가 가능하다 —
// next/dist/docs/01-app/02-guides/data-security.md). 그 경로로 들어오면
// 화면 쪽 확인 절차(preset 화이트리스트·확인 체크박스·일일 한도·
// operation_events 기록)를 전부 건너뛴 채 GH_WORKFLOW_TOKEN으로 임의
// 워크플로가 실행된다. 액션이 아닌 일반 서버 모듈에 두어 그 진입점 자체를
// 없앤다. 인증은 지금처럼 호출하는 액션이 requireOperator로 책임진다.
// ------------------------------------------------------------

const GITHUB_REPO = "jungsoo7496-blip/robot-intel";

export async function dispatchWorkflow(
  workflow: string,
  inputs: Record<string, string> = {},
) {
  const token = process.env.GH_WORKFLOW_TOKEN;
  if (!token) {
    throw new Error("GH_WORKFLOW_TOKEN이 설정되지 않았습니다");
  }
  const res = await fetch(
    `https://api.github.com/repos/${GITHUB_REPO}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ ref: "main", inputs }),
    },
  );
  if (!res.ok) {
    throw new Error(`워크플로 실행 요청 실패 (${res.status})`);
  }
}

/**
 * 해당 워크플로가 지금 실행 중(또는 대기 중)인가 — GitHub에 직접 묻는다.
 *
 * 2026-08-12 수정: 이전에는 "직전 실행 시각으로부터 15분"이라는 고정 쿨다운을
 * 걸었는데, 앞 실행이 3분 만에 끝나도 12분을 더 막았다(사용자 제보). 막아야 할
 * 것은 '동시 실행'이지 '15분'이 아니므로 실제 상태를 확인한다.
 * 조회에 실패하면(토큰·네트워크) 막지 않는다 — 관측 실패가 기능 정지가 되면 안 된다.
 */
export async function isWorkflowBusy(workflow: string): Promise<boolean> {
  const token = process.env.GH_WORKFLOW_TOKEN;
  if (!token) return false;
  try {
    const res = await fetch(
      `https://api.github.com/repos/${GITHUB_REPO}/actions/workflows/${workflow}` +
        `/runs?per_page=10&exclude_pull_requests=true`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
        },
        cache: "no-store",
      },
    );
    if (!res.ok) return false;
    const body = (await res.json()) as {
      workflow_runs?: { status?: string }[];
    };
    return (body.workflow_runs ?? []).some(
      (r) =>
        r.status === "queued" ||
        r.status === "in_progress" ||
        r.status === "waiting" ||
        r.status === "requested" ||
        r.status === "pending",
    );
  } catch {
    return false;
  }
}
