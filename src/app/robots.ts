import type { MetadataRoute } from "next";

/**
 * 내부용 사이트 색인 정책: URL을 아는 사람의 접속은 허용하되
 * 검색엔진 크롤링·색인은 전면 차단한다 (layout.tsx의 robots meta와 한 쌍).
 * robots.txt는 보안 장치가 아니라 검색 노출 방지 용도다.
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      disallow: "/",
    },
  };
}
