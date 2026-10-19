"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AuthGate } from "@/components/AuthGate";
import { Header } from "@/components/Header";
import { api, ApiError, Environment } from "@/lib/api";

function DoctorPanel({ doctor }: { doctor: Environment["doctor"] }) {
  const color: Record<string, string> = {
    OK: "text-green-700", WARN: "text-amber-700", FAIL: "text-red-700", INFO: "text-neutral-500",
  };
  return (
    <div className="mb-6 rounded-lg border border-neutral-200 bg-white p-4 text-sm">
      <div className="mb-2 font-medium text-neutral-700">환경 상태</div>
      <ul className="space-y-1">
        {doctor.map((d, i) => (
          <li key={i} className={color[d.status] ?? "text-neutral-600"}>
            [{d.status}] {d.name}: {d.detail}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function NewRunPage() {
  const router = useRouter();
  const [env, setEnv] = useState<Environment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [targets, setTargets] = useState<string[]>([]);
  const [operator, setOperator] = useState("");
  const [checklist, setChecklist] = useState<string>("");
  const [useLlm, setUseLlm] = useState(true);
  const [autonomy, setAutonomy] = useState("approval");

  useEffect(() => {
    api.get<Environment>("/api/environment").then(setEnv).catch((e) => setError(e.message));
  }, []);

  function toggleTarget(id: string) {
    setTargets((t) => (t.includes(id) ? t.filter((x) => x !== id) : [...t, id]));
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (targets.length === 0) {
      setError("대상을 하나 이상 선택하세요");
      return;
    }
    if (!checklist && !useLlm) {
      setError("체크리스트 없이 실행하려면 'LLM 자율 탐색 사용'이 필요함");
      return;
    }
    setLoading(true);
    try {
      const run = await api.post<{ id: string }>("/api/runs", {
        targets,
        operator: operator || "operator",
        checklist: checklist || null,
        use_llm: useLlm,
        autonomy,
      });
      router.push(`/runs/${run.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "실행 생성 실패");
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthGate>
      <Header />
      <div className="mx-auto max-w-2xl p-6">
        <h1 className="mb-4 text-lg font-semibold">새 진단 환경 설정</h1>
        {error && <p className="mb-4 rounded border border-red-200 bg-red-50 p-2 text-sm text-red-700">{error}</p>}
        {!env ? (
          <p className="text-sm text-neutral-500">불러오는 중...</p>
        ) : (
          <>
            <DoctorPanel doctor={env.doctor} />
            <form onSubmit={onSubmit} className="space-y-5 rounded-lg border border-neutral-200 bg-white p-6">
              <div>
                <label className="mb-1 block text-sm font-medium text-neutral-700">대상(scope.json)</label>
                {env.targets.length === 0 && <p className="text-sm text-neutral-500">등록된 대상이 없음</p>}
                <div className="space-y-2">
                  {env.targets.map((t) => (
                    <label key={t.target_id} className="flex items-start gap-2 text-sm">
                      <input
                        type="checkbox"
                        className="mt-1"
                        checked={targets.includes(t.target_id)}
                        onChange={() => toggleTarget(t.target_id)}
                      />
                      <span>
                        <span className="font-mono">{t.target_id}</span>{" "}
                        <span className="text-neutral-500">
                          ({t.type}:{t.value}{t.platform ? `, ${t.platform}` : ""})
                        </span>
                        {t.description && <div className="text-xs text-neutral-400">{t.description}</div>}
                      </span>
                    </label>
                  ))}
                </div>
              </div>

              <div>
                <label className="mb-1 block text-sm font-medium text-neutral-700">운영자</label>
                <input
                  className="w-full rounded border border-neutral-300 px-3 py-2 text-sm"
                  value={operator}
                  onChange={(e) => setOperator(e.target.value)}
                  placeholder="이름"
                />
              </div>

              <div>
                <label className="mb-1 block text-sm font-medium text-neutral-700">체크리스트(선택)</label>
                <select
                  className="w-full rounded border border-neutral-300 px-3 py-2 text-sm"
                  value={checklist}
                  onChange={(e) => setChecklist(e.target.value)}
                >
                  <option value="">없음 — 자율 탐색만</option>
                  {env.checklists.map((c) => (
                    <option key={`${c.checklist_id}@${c.version}`} value={`${c.checklist_id}@${c.version}`}>
                      {c.checklist_id}@{c.version}
                    </option>
                  ))}
                </select>
              </div>

              <div className="flex items-center gap-2">
                <input
                  id="use_llm"
                  type="checkbox"
                  checked={useLlm}
                  onChange={(e) => setUseLlm(e.target.checked)}
                />
                <label htmlFor="use_llm" className="text-sm text-neutral-700">
                  LLM 자율 탐색 사용(체크리스트 없이 실행하려면 필수)
                </label>
              </div>

              <div>
                <label className="mb-1 block text-sm font-medium text-neutral-700">자율성</label>
                <div className="flex gap-4 text-sm">
                  {env.autonomy_levels.map((a) => (
                    <label key={a} className="flex items-center gap-1">
                      <input
                        type="radio"
                        name="autonomy"
                        value={a}
                        checked={autonomy === a}
                        onChange={() => setAutonomy(a)}
                      />
                      {a === "approval" ? "approval(항상 승인)" : "full(범위 내 자동)"}
                    </label>
                  ))}
                </div>
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full rounded bg-neutral-900 px-3 py-2 text-sm font-medium text-white hover:bg-neutral-700 disabled:opacity-50"
              >
                {loading ? "생성 중..." : "진단 환경 생성"}
              </button>
            </form>
          </>
        )}
      </div>
    </AuthGate>
  );
}
