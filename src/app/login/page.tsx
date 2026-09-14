"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { createClient } from "@/lib/supabase/client";

/** 운영자 전용 로그인 (D-001). 직원 열람은 로그인이 필요 없다. */
function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // 오픈 리다이렉트 방지: /admin 하위 경로만 허용 (외부 리뷰 P1-9)
  const requested = searchParams.get("next");
  const nextPath =
    requested?.startsWith("/admin") && !requested.startsWith("//")
      ? requested
      : "/admin";

  async function signIn(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const supabase = createClient();
    // 아이디만 입력해도 되도록 내부 계정 도메인을 자동으로 붙인다 (D-001)
    const loginEmail = email.includes("@")
      ? email
      : `${email.trim()}@robotreport.local`;
    const { error } = await supabase.auth.signInWithPassword({
      email: loginEmail,
      password,
    });
    setLoading(false);
    if (error) {
      setError("로그인에 실패했습니다. 아이디와 비밀번호를 확인하세요.");
      return;
    }
    router.replace(nextPath);
    router.refresh();
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center px-4">
      <h1 className="text-xl font-bold">운영자 로그인</h1>
      <p className="mt-1 text-sm text-black/60 dark:text-white/60">
        KIRO 로봇 인텔리전스 관리 기능은 운영자 계정 전용입니다.
        열람은 로그인 없이 가능합니다.
      </p>
      {searchParams.get("expired") && (
        <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-500/15 dark:text-amber-200">
          보안을 위해 로그인 후 24시간이 지나면 다시 로그인해야 합니다.
        </p>
      )}

      <form onSubmit={signIn} className="mt-6 space-y-3">
        <input
          type="text"
          required
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="운영자 아이디"
          className="w-full rounded border border-black/20 px-3 py-2 dark:border-white/25 dark:bg-transparent"
        />
        <input
          type="password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="비밀번호"
          className="w-full rounded border border-black/20 px-3 py-2 dark:border-white/25 dark:bg-transparent"
        />
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button
          type="submit"
          disabled={loading}
          className="w-full rounded bg-foreground px-3 py-2 font-medium text-background disabled:opacity-50"
        >
          {loading ? "처리 중…" : "로그인"}
        </button>
      </form>

      <Link href="/" className="mt-6 text-center text-sm underline">
        사이트로 돌아가기
      </Link>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
