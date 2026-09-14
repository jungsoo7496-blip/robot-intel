/** 접근 게이트 안내 (D-003): 포털 링크 없이 직접 접근한 경우. */
export default function BlockedPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4 text-center">
      <h1 className="text-xl font-bold">KIRO 내부 전용 서비스입니다</h1>
      <p className="mt-3 text-sm text-black/70 dark:text-white/70">
        이 사이트는 KIRO 내부 포털을 통해서만 접속할 수 있습니다.
        포털에 게시된 링크로 다시 접속해 주세요.
      </p>
      <p className="mt-2 text-sm text-black/50 dark:text-white/50">
        접속에 문제가 있으면 운영 책임자에게 문의하세요.
      </p>
    </main>
  );
}
