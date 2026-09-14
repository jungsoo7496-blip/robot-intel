"use client";

import { createBrowserClient } from "@supabase/ssr";

import { publicEnv } from "@/lib/env";

/** 브라우저용 Supabase 클라이언트 — anon 키만 사용한다 (NFR-004). */
export function createClient() {
  const { supabaseUrl, supabaseAnonKey } = publicEnv();
  return createBrowserClient(supabaseUrl, supabaseAnonKey);
}
