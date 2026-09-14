import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async redirects() {
    return [
      // 아카이브 메뉴는 최신 동향의 기간 선택으로 통합됨 (사용자 피드백)
      { source: "/archive", destination: "/trends", permanent: false },
    ];
  },
};

export default nextConfig;
