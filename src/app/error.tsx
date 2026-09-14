"use client";

/** 전역 오류 화면 — 장애 시 빈 화면 대신 상태를 안내한다 (tasks §10.2). */
export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4 text-center">
      <h1 className="text-xl font-bold">일시적인 오류가 발생했습니다</h1>
      <p className="mt-3 text-sm text-black/70 dark:text-white/70">
        잠시 후 다시 시도해 주세요. 문제가 계속되면 운영 책임자에게 알려주세요.
      </p>
      <button
        onClick={reset}
        className="mx-auto mt-6 rounded border border-black/20 px-4 py-2 text-sm dark:border-white/25"
      >
        다시 시도
      </button>
    </main>
  );
}
