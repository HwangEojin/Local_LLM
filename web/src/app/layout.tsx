import type { Metadata } from "next";
import "./globals.css";
import { ToastProvider } from "@/components/ui";

export const metadata: Metadata = {
  title: "진단 워크벤치",
  description: "자율 AI 취약점 진단 워크벤치 — 로컬 대시보드",
};

// 첫 페인트 전에 저장된 테마를 적용해 깜빡임을 막는다(기본은 다크).
const THEME_INIT = `try{var t=localStorage.getItem("wb-theme");if(t==="light")document.documentElement.dataset.theme="light"}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" className="h-full antialiased" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
      </head>
      <body className="flex min-h-full flex-col bg-canvas font-sans text-ink">
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}
