"use client";

// 좌측 사이드바 — 상단 탭 바(Header)를 대체한다. "작업"/"시스템" 두 그룹으로 나눈다(UPGRADE_PLAN.md B1).
// 아직 독립 페이지가 없는 항목(자산/발견사항/트래픽, 시스템 세부 화면들)은 B2~B4에서 추가될 때 여기 추가한다 —
// 지금은 실제로 존재하는 라우트만 링크한다.
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  LayoutDashboard,
  List,
  LogOut,
  OctagonAlert,
  Plus,
  MessageSquareText,
  Radar,
  ScrollText,
  Settings,
  ShieldAlert,
  ShieldCheck,
  SunMoon,
  Terminal,
} from "lucide-react";

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
      className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-muted transition-colors hover:bg-raised hover:text-ink"
      aria-label="다크/라이트 테마 전환"
      title="다크/라이트 테마 전환"
    >
      <SunMoon className={ICON} aria-hidden />
      테마
    </button>
  );
}

/** 중지 스위치가 켜져 있으면 모든 화면 맨 위에 계속 보여 준다(도구 호출이 전부 거부되는 상태이므로). */
export function KillSwitchBanner() {
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

type NavItem = { href: string; label: string; Icon: typeof List; active: (path: string) => boolean };

const WORK_ITEMS: NavItem[] = [
  { href: "/dashboard", label: "대시보드", Icon: LayoutDashboard, active: (p) => p === "/dashboard" },
  { href: "/runs", label: "실행", Icon: List, active: (p) => p === "/runs" || (p.startsWith("/runs/") && p !== "/runs/new") },
  { href: "/findings", label: "발견 사항", Icon: ShieldAlert, active: (p) => p === "/findings" },
  { href: "/traffic", label: "트래픽", Icon: Radar, active: (p) => p === "/traffic" },
  { href: "/llm-records", label: "LLM 호출 기록", Icon: MessageSquareText, active: (p) => p === "/llm-records" },
  { href: "/commands", label: "명령 이력", Icon: Terminal, active: (p) => p === "/commands" },
];

const SYSTEM_ITEMS: NavItem[] = [
  { href: "/settings", label: "설정", Icon: Settings, active: (p) => p === "/settings" },
  { href: "/audit", label: "감사 로그", Icon: ScrollText, active: (p) => p === "/audit" },
];

function NavGroup({ title, items, path }: { title: string; items: NavItem[]; path: string }) {
  return (
    <div>
      <div className="px-2.5 pb-1.5 text-xs font-semibold uppercase tracking-wide text-faint">{title}</div>
      <nav className="space-y-0.5">
        {items.map(({ href, label, Icon, active }) => (
          <Link
            key={href}
            href={href}
            aria-current={active(path) ? "page" : undefined}
            className={`flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm transition-colors ${
              active(path) ? "bg-raised text-ink" : "text-muted hover:text-ink"
            }`}
          >
            <Icon className={ICON} aria-hidden />
            {label}
          </Link>
        ))}
      </nav>
    </div>
  );
}

export function Sidebar() {
  const router = useRouter();
  const path = usePathname();

  async function logout() {
    await api.post("/api/auth/logout");
    router.replace("/login");
  }

  return (
    <aside className="flex h-screen w-56 shrink-0 flex-col justify-between border-r border-line bg-surface/60 px-2 py-3">
      <div className="space-y-5">
        <Link href="/runs" className="flex items-center gap-2 px-2 text-sm font-semibold tracking-tight text-ink">
          <ShieldCheck className="h-5 w-5 text-accent" aria-hidden />
          진단 워크벤치
        </Link>
        <Link
          href="/runs/new"
          aria-current={path === "/runs/new" ? "page" : undefined}
          className={`flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors ${
            path === "/runs/new" ? "bg-accent/15 text-accent" : "bg-accent text-[var(--on-accent,#fff)] hover:opacity-90"
          }`}
        >
          <Plus className={ICON} aria-hidden />
          새 진단
        </Link>
        <NavGroup title="작업" items={WORK_ITEMS} path={path} />
        <NavGroup title="시스템" items={SYSTEM_ITEMS} path={path} />
      </div>
      <div className="space-y-0.5">
        <ThemeToggle />
        <button
          onClick={logout}
          className="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-muted transition-colors hover:bg-raised hover:text-ink"
        >
          <LogOut className={ICON} aria-hidden />
          로그아웃
        </button>
      </div>
    </aside>
  );
}
