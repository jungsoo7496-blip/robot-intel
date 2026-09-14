import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4 text-center">
      <h1 className="text-xl font-bold">페이지를 찾을 수 없습니다</h1>
      <p className="mt-3 text-sm text-black/70 dark:text-white/70">
        주소가 잘못되었거나 삭제된 페이지입니다.
      </p>
      <Link href="/" className="mt-6 text-sm underline">
        홈으로 돌아가기
      </Link>
    </main>
  );
}
