"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { api, ApiError } from "@/lib/api";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get<{ user: string }>("/api/auth/me")
      .then(() => {
        if (!cancelled) setReady(true);
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 401) {
          router.replace("/login");
        } else {
          setReady(true); // 다른 오류(네트워크 등)는 페이지에서 보이게 둔다
        }
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (!ready) return <div className="p-6 text-sm text-neutral-500">확인 중...</div>;
  return <>{children}</>;
}
