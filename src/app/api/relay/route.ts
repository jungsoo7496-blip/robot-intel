import { NextRequest } from "next/server";

/**
 * 국내 리전 경유 (2026-08-19) — 일부 국내 공공 사이트 전용.
 *
 * 이 앱은 vercel.json regions=["icn1"] 로 서울에서 실행되므로, 배치가
 * 이 라우트를 경유하면 국내에서 접근하는 것과 같다. 대상 호스트는 아래
 * 화이트리스트로 고정한다.
 *
 * 안전장치 (열린 프록시가 되지 않도록):
 * - RELAY_TOKEN 헤더가 일치해야 한다 (GitHub Secrets와 공유)
 * - 대상 호스트는 고정 화이트리스트만 (https 한정)
 * - 응답 본문은 3.5MB에서 자른다 (배치의 Range 검사는 64KB만 쓴다 —
 *   전체 파일 전송이 목적이 아니다)
 */

const ALLOWED_HOSTS = new Set([
  "policy.nl.go.kr",
  "www.alio.go.kr",
  "alio.go.kr",
]);
const MAX_BODY_BYTES = 3_500_000;
const UPSTREAM_TIMEOUT_MS = 8_500; // Vercel 함수 한도(10초) 안에서 응답

export const dynamic = "force-dynamic";

async function relay(req: NextRequest, method: "GET" | "HEAD") {
  const token = process.env.RELAY_TOKEN;
  if (!token || req.headers.get("x-relay-token") !== token) {
    return new Response("forbidden", { status: 403 });
  }

  const target = req.nextUrl.searchParams.get("url");
  if (!target) {
    return new Response("missing url", { status: 400 });
  }
  let parsed: URL;
  try {
    parsed = new URL(target);
  } catch {
    return new Response("bad url", { status: 400 });
  }
  if (parsed.protocol !== "https:" || !ALLOWED_HOSTS.has(parsed.hostname)) {
    return new Response("host not allowed", { status: 400 });
  }

  const headers: Record<string, string> = {
    "User-Agent": "Mozilla/5.0 (compatible; KIRO-RobotIntel/0.1)",
  };
  const range = req.headers.get("range");
  if (range) headers.Range = range;

  let upstream: Response;
  try {
    upstream = await fetch(parsed, {
      method,
      headers,
      redirect: "follow",
      cache: "no-store",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    return new Response("upstream unreachable", { status: 502 });
  }

  const out = new Headers();
  for (const name of ["content-type", "content-disposition", "content-range"]) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  out.set("x-relay-final-url", upstream.url);

  if (method === "HEAD" || !upstream.body) {
    return new Response(null, { status: upstream.status, headers: out });
  }

  // 본문을 상한까지만 읽는다 — content-length는 다시 계산되도록 전달하지 않음
  const reader = upstream.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  while (received < MAX_BODY_BYTES) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.byteLength;
  }
  await reader.cancel().catch(() => {});
  const body = new Blob(chunks as BlobPart[]);
  return new Response(body, { status: upstream.status, headers: out });
}

export async function GET(req: NextRequest) {
  return relay(req, "GET");
}

export async function HEAD(req: NextRequest) {
  return relay(req, "HEAD");
}
