/** 아직 구현되지 않은 화면의 자리표시자. 해당 구현 Stage를 명시한다. */
export function StagePlaceholder({
  title,
  stage,
  description,
}: {
  title: string;
  stage: string;
  description: string;
}) {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">{title}</h1>
      <div className="rounded-lg border border-dashed border-black/20 p-6 text-sm text-black/60 dark:border-white/25 dark:text-white/60">
        <p>{description}</p>
        <p className="mt-2 font-medium">구현 예정: {stage}</p>
      </div>
    </div>
  );
}
