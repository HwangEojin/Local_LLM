"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { LayoutDashboard, List, LogOut, OctagonAlert, Plus, ScrollText, Settings, ShieldCheck, SunMoon } from "lucide-react";

import { api, KillSwitch } from "@/lib/api";
import { formatTime } from "@/lib/labels";
import { usePolling } from "@/lib/usePolling";

const ICON = "h-4 w-4 shrink-0";

function ThemeToggle() {
  // 테마는 <html data-theme> 가 단일 출처다(초기값은 layout 의 인라인 스크립트가 설정). 상태를 따로 두지 않는다.
  function toggle() {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("wb-theme", next);
    } catch {
      // 저장소를 못 쓰는 환경이면 이번 세션에만 적용
    }
  }
  return (
    <button
      onClick={toggle}
      className="rounded-md p-2 text-muted transition-colors hover:bg-raised hover:text-ink"
      aria-label="다크/라이트 테마 전환"
      title="다크/라이트 테마 전환"
    >
      <SunMoon className={ICON} aria-hidden />
    </button>
  );
}

/** 중지 스위치가 켜져 있으면 모든 화면 맨 위에 계속 보여 준다(도구 호출이 전부 거부되는 상태이므로). */
function KillSwitchBanner() {
  const [ks, setKs] = useState<KillSwitch | null>(null);
  const load = useCallback(() => {
    api.get<KillSwitch>("/api/admin/kill-switch").then(setKs).catch(() => {});
  }, []);
  useEffect(load, [load]);
  usePolling(load, 10000);
  // 설정 화면에서 켜거나 해제하면 폴링을 기다리지 않고 바로 갱신한다.
  useEffect(() => {
    window.addEventListener("wb-killswitch", load);
    return () => window.removeEventListener("wb-killswitch", load);
  }, [load]);
  if (!ks?.active) return null;
  return (
    <div role="alert" className="flex items-center justify-between gap-3 border-b border-danger/40 bg-danger/15 px-6 py-2 text-sm text-danger">
      <span className="flex items-center gap-2">
        <OctagonAlert className={ICON} aria-hidden />
        중지 스위치가 켜져 있습니다. 모든 도구 호출이 차단됩니다.
        {ks.reason && <span className="text-danger/80">사유: {ks.reason}</span>}
        {ks.by && <span className="text-danger/80">· {ks.by} {ks.at ? formatTime(ks.at) : ""}</span>}
      </span>
      <Link href="/settings#kill-switch" className="font-medium underline underline-offset-2">
        관리
      </Link>
    </div>
  );
}

export function Header() {
  const router = useRouter();
  const path = usePathname();

  async function logout() {
    await api.post("/api/auth/logout");
    router.replace("/login");
  }

  const link = (href: string, label: string, active: boolean, Icon: typeof List) => (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-sm transition-colors ${
        active ? "bg-raised text-ink" : "text-muted hover:text-ink"
      }`}
    >
      <Icon className={ICON} aria-hidden />
      {label}
    </Link>
  );

  return (
    <>
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-surface/95 px-6 py-2.5 backdrop-blur">
        <div className="flex items-center gap-6">
          <Link href="/runs" className="flex items-center gap-2 text-sm font-semibold tracking-tight text-ink">
            <ShieldCheck className="h-5 w-5 text-accent" aria-hidden />
            진단 워크벤치
          </Link>
          <nav className="flex items-center gap-1">
            {link("/dashboard", "대시보드", path === "/dashboard", LayoutDashboard)}
            {link("/runs", "진단 목록", path === "/runs" || (path.startsWith("/runs/") && path !== "/runs/new"), List)}
            {link("/runs/new", "새 진단", path === "/runs/new", Plus)}
            {link("/settings", "설정", path === "/settings", Settings)}
            {link("/audit", "감사 로그", path === "/audit", ScrollText)}
          </nav>
        </div>
        <div className="flex items-center gap-1">
          <ThemeToggle />
          <button
            onClick={logout}
            className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-muted transition-colors hover:bg-raised hover:text-ink"
          >
            <LogOut className={ICON} aria-hidden />
            로그아웃
          </button>
        </div>
      </header>
      <KillSwitchBanner />
    </>
  );
}
