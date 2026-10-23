import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 백엔드(127.0.0.1)와 같은 사이트여야 로그인 쿠키(SameSite=Lax)가 전송되므로 UI 도 127.0.0.1 로 연다.
  // 개발 서버는 기본으로 localhost 외 출처의 요청(HMR·하이드레이션)을 막아서 허용해 둔다.
  allowedDevOrigins: ["127.0.0.1"],
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
