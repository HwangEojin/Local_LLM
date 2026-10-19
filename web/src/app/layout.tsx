import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Workbench",
  description: "자율 AI 취약점 진단 워크벤치 — 로컬 대시보드",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-neutral-50 text-neutral-900 font-sans">{children}</body>
    </html>
  );
}
