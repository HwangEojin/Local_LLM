import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 전부 클라이언트에서 FastAPI 를 호출하는 로그인 뒤 대시보드라 서버 캐싱 실험 기능은 끈다.
  // Tailwind v4 CSS 처리에는 이 로더 규칙이 필요함(create-next-app 기본값, 지우면 @theme 등이 안 먹음).
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
