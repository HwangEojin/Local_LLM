"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { api } from "@/lib/api";

export function Header() {
  const router = useRouter();

  async function logout() {
    await api.post("/api/auth/logout");
    router.replace("/login");
  }

  return (
    <header className="flex items-center justify-between border-b border-neutral-200 bg-white px-6 py-3">
      <Link href="/runs" className="text-sm font-semibold">
        Workbench
      </Link>
      <button onClick={logout} className="text-sm text-neutral-500 hover:text-neutral-900">
        로그아웃
      </button>
    </header>
  );
}
