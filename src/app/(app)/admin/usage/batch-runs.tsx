import { shortenNote, workflowLabel } from "./labels";
import type { BatchRun } from "./usage-data";

const STATUS_LABELS: Record<string, string> = {
  SUCCESS: "성공",
  FAILURE: "실패",
  PARTIAL: "일부",
  RUNNING: "실행 중",
};

function statusClass(status: string): string {
  if (status === "FAILURE") return "text-red-600 dark:text-red-400";
  if (status === "SUCCESS") return "text-green-700 dark:text-green-400";
  if (status === "RUNNING") return "text-amber-600 dark:text-amber-400";
  return "";
}

function duration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds}초`;
  return `${Math.floor(seconds / 60)}분 ${seconds % 60}초`;
}

/** 한국 시각 "09/08 09:16". toLocaleString의 "9. 8. 오전 09:16"보다 표에서 읽기 쉽다. */
function startedAt(iso: string): string {
  const kst = new Date(new Date(iso).getTime() + 9 * 3_600_000);
  const p = (n: number) => String(n).padStart(2, "0");
  return (
    `${p(kst.getUTCMonth() + 1)}/${p(kst.getUTCDate())} ` +
    `${p(kst.getUTCHours())}:${p(kst.getUTCMinutes())}`
  );
}

/** 배치 실행 이력 (workflow_usage). */
export function BatchRunsTable({ runs }: { runs: BatchRun[] }) {
  if (runs.length === 0) {
    return (
      <p className="mt-2 text-sm text-black/50 dark:text-white/50">
        이 조건에 맞는 실행 기록이 없습니다.
      </p>
    );
  }

  return (
    <div className="mt-3 overflow-x-auto rounded-lg border border-black/10 dark:border-white/15">
      <table className="w-full min-w-[720px] text-sm">
        <thead className="border-b border-black/10 text-left text-black/60 dark:border-white/15 dark:text-white/60">
          <tr>
            <th className="p-2 font-medium">워크플로</th>
            <th className="p-2 font-medium">시작 (한국 시각)</th>
            <th className="p-2 font-medium">소요</th>
            <th className="p-2 text-right font-medium">수집</th>
            <th className="p-2 text-right font-medium">분석</th>
            <th className="p-2 text-right font-medium">호출</th>
            <th className="p-2 text-right font-medium">남은 대기</th>
            <th className="p-2 font-medium">상태</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr
              key={r.id}
              className="border-b border-black/5 last:border-0 dark:border-white/10"
            >
              <td className="p-2 align-top">
                {workflowLabel(r.workflow_name)}
                {r.note && (
                  <div className="text-xs text-black/50 dark:text-white/50">
                    {shortenNote(r.note)}
                  </div>
                )}
              </td>
              <td className="whitespace-nowrap p-2 align-top tabular-nums">
                {startedAt(r.started_at)}
              </td>
              <td className="whitespace-nowrap p-2 align-top tabular-nums">
                {duration(r.duration_seconds)}
              </td>
              <td className="p-2 text-right align-top tabular-nums">
                {r.collected_count || "—"}
              </td>
              <td className="p-2 text-right align-top tabular-nums">
                {r.analyzed_count || "—"}
              </td>
              <td className="p-2 text-right align-top tabular-nums">
                {r.api_call_count || "—"}
              </td>
              <td className="p-2 text-right align-top tabular-nums">
                {r.remaining_pending_count?.toLocaleString("ko-KR") ?? "—"}
              </td>
              <td className={`whitespace-nowrap p-2 align-top ${statusClass(r.status)}`}>
                {STATUS_LABELS[r.status] ?? r.status}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
