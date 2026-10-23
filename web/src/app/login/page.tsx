"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { LogIn } from "lucide-react";

import { btnPrimary, inputCls } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api.post("/api/auth/login", { username, password });
      router.replace("/runs");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "로그인에 실패했습니다");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex flex-1 items-center justify-center p-6">
      <form onSubmit={onSubmit} className="w-full max-w-sm rounded-lg border border-line bg-surface p-6">
        <h1 className="text-lg font-semibold">진단 워크벤치</h1>
        <p className="mb-6 mt-1 text-sm text-muted">자율 AI 취약점 진단 · 로컬 대시보드</p>
        <label htmlFor="username" className="mb-1 block text-sm font-medium text-muted">
          아이디
        </label>
        <input
          id="username"
          autoComplete="username"
          className={`${inputCls} mb-4`}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
        />
        <label htmlFor="password" className="mb-1 block text-sm font-medium text-muted">
          비밀번호
        </label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          className={`${inputCls} mb-4`}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && (
          <p role="alert" className="mb-4 text-sm text-danger">
            {error}
          </p>
        )}
        <button type="submit" disabled={loading || !username || !password} className={`${btnPrimary} w-full`}>
          <LogIn className="h-4 w-4" aria-hidden />
          {loading ? "로그인 중…" : "로그인"}
        </button>
      </form>
    </main>
  );
}
