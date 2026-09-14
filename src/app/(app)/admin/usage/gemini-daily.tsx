import { modelLabel } from "./labels";
import { MODEL_RPD_LIMIT } from "./quota-math";
import { callTotal, dayCounts, type GeminiHistory } from "./usage-data";

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

function dayLabel(date: string): string {
  const d = new Date(`${date}T00:00:00Z`);
  return `${date.slice(5).replace("-", "/")} (${WEEKDAYS[d.getUTCDay()]})`;
}

/** 한도 대비 막대 — 차트 라이브러리 없이 CSS만 쓴다. */
function QuotaBar({ calls }: { calls: number }) {
  const pct = (calls / MODEL_RPD_LIMIT) * 100;
  const fill =
    pct >= 100
      ? "bg-red-500"
      : pct >= 90
        ? "bg-amber-500"
        : "bg-emerald-500";
  return (
    <div className="mt-1 flex items-center gap-2">
      <div className="h-1.5 w-24 rounded-full bg-black/10 dark:bg-white/15">
        <div
          className={`h-full rounded-full ${fill}`}
          style={{ width: `${Math.min(100, Math.max(2, pct))}%` }}
        />
      </div>
      <span className="text-xs tabular-nums text-black/45 dark:text-white/45">
        {Math.round(pct)}%
      </span>
    </div>
  );
}

/**
 * Gemini 일별 사용량 — 쿼터일(UTC-8)별 모델 호출 수와 한도 대비 비율.
 * 실패는 429(한도)와 오류(5xx 등)를 나눠 보여준다.
 */
export function GeminiDailyTable({
  history,
  articleModels,
  softLimit,
  todayQuotaDate,
}: {
  history: GeminiHistory;
  articleModels: string[];
  softLimit: number;
  /** 진행 중인 쿼터일 — 아직 안 끝난 날이라 가동률 평균에서 뺀다. */
  todayQuotaDate: string;
}) {
  const { days, models } = history;
  if (days.length === 0) {
    return (
      <p className="mt-2 text-sm text-black/50 dark:text-white/50">
        이 기간에 기록된 호출이 없습니다.
      </p>
    );
  }

  // 가동률의 분자·분모를 같은 모델 집합으로 맞춘다. 표의 '합계'에는 브리프
  // 모델(별도 쿼터 풀)이 섞여 있어 그대로 나누면 가동률이 부풀려진다.
  const articleCalls = days.map((d) => ({
    date: d.date,
    calls: articleModels.reduce((s, m) => s + callTotal(dayCounts(d, m)), 0),
  }));
  // 평균에서 빼는 날: (1) 아직 진행 중인 오늘, (2) 호출이 0인 날 —
  // 배치가 통째로 걸린 날을 섞으면 '가동 중일 때의 가동률'이 안 나온다.
  const past = articleCalls.filter((d) => d.date !== todayQuotaDate);
  const active = past.filter((d) => d.calls > 0);
  const idleDays = past.length - active.length;
  const avgPerDay = active.length
    ? Math.round(active.reduce((s, d) => s + d.calls, 0) / active.length)
    : 0;
  // 한도는 모델당 500 — 분석에 쓰는 모델 수만큼이 하루에 쓸 수 있는 총량이다.
  const dailyCapacity = MODEL_RPD_LIMIT * Math.max(1, articleModels.length);

  return (
    <>
      <p className="mt-1 text-sm text-black/60 dark:text-white/60">
        {active.length === 0 ? (
          <>
            최근 {days.length}일 중 분석 호출이 있었던 지난 날이 없어 가동률을
            낼 수 없습니다.
          </>
        ) : (
          <>
            분석 모델 {articleModels.length}개가 돌아간 {active.length}일 평균
            하루 {avgPerDay.toLocaleString("ko-KR")}회 — 쓸 수 있는{" "}
            {dailyCapacity.toLocaleString("ko-KR")}회의{" "}
            <strong>{Math.round((avgPerDay / dailyCapacity) * 100)}%</strong>.
            진행 중인 오늘
            {idleDays > 0 && `과 호출이 0인 ${idleDays}일`}은 평균에서 뺐습니다.
          </>
        )}
      </p>
      <p className="mt-1 text-xs text-black/50 dark:text-white/50">
        막대와 가동률은 분석 모델만 셉니다 — 표의 &lsquo;합계&rsquo;에 있는
        브리프 모델은 별도 쿼터라 섞으면 비율이 부풀려집니다. 막대는 모델당
        하루 한도 {MODEL_RPD_LIMIT}회 기준이고, 배치는 그보다 낮은 내부
        목표(하루 {softLimit}회)에서 예약분을 뺀 만큼에 닿으면 스스로 멈춥니다.
      </p>

      <div className="mt-3 overflow-x-auto rounded-lg border border-black/10 dark:border-white/15">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="border-b border-black/10 text-left text-black/60 dark:border-white/15 dark:text-white/60">
            <tr>
              <th className="p-2 font-medium">쿼터일</th>
              {models.map((m) => (
                <th key={m} className="p-2 font-medium">
                  {modelLabel(m)}
                </th>
              ))}
              <th className="p-2 text-right font-medium">합계</th>
            </tr>
          </thead>
          <tbody>
            {days.map((day) => (
              <tr
                key={day.date}
                className="border-b border-black/5 last:border-0 dark:border-white/10"
              >
                <td className="whitespace-nowrap p-2 tabular-nums">
                  {dayLabel(day.date)}
                </td>
                {models.map((m) => {
                  const c = dayCounts(day, m);
                  const calls = callTotal(c);
                  const tracked = articleModels.includes(m);
                  return (
                    <td key={m} className="p-2 align-top">
                      <span className="tabular-nums">
                        {calls.toLocaleString("ko-KR")}
                      </span>
                      {tracked && calls > 0 && <QuotaBar calls={calls} />}
                      {(c.limited > 0 || c.error > 0) && (
                        <div className="mt-0.5 text-xs text-red-600 dark:text-red-400">
                          {c.limited > 0 && `429 ${c.limited}`}
                          {c.limited > 0 && c.error > 0 && " · "}
                          {c.error > 0 && `오류 ${c.error}`}
                        </div>
                      )}
                    </td>
                  );
                })}
                <td className="p-2 text-right align-top tabular-nums">
                  {day.total.toLocaleString("ko-KR")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
