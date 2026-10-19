"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { AuthGate } from "@/components/AuthGate";
import { Header } from "@/components/Header";
import { StatusBadge } from "@/components/StatusBadge";
import { api, Run } from "@/lib/api";

function RunsTable() {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<Run[]>("/api/runs")
      .then(setRuns)
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="p-6 text-sm text-red-600">{error}</p>;
  if (!runs) return <p className="p-6 text-sm text-neutral-500">불러오는 중...</p>;

  return (
    <div className="p-6">
      <h1 className="mb-4 text-lg font-semibold">실행(Run) 목록</h1>
      <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-neutral-50 text-left text-neutral-500">
            <tr>
              <th className="px-4 py-2 font-medium">ID</th>
              <th className="px-4 py-2 font-medium">상태</th>
              <th className="px-4 py-2 font-medium">운영자</th>
              <th className="px-4 py-2 font-medium">모드</th>
              <th className="px-4 py-2 font-medium">자율성</th>
              <th className="px-4 py-2 font-medium">발견 사항</th>
              <th className="px-4 py-2 font-medium">생성 시각</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className="border-t border-neutral-100 hover:bg-neutral-50">
                <td className="px-4 py-2">
                  <Link href={`/runs/${r.id}`} className="font-mono text-xs text-blue-700 hover:underline">
                    {r.id.slice(0, 8)}
                  </Link>
                </td>
                <td className="px-4 py-2">
                  <StatusBadge value={r.status} />
                </td>
                <td className="px-4 py-2">{r.operator}</td>
                <td className="px-4 py-2">{r.has_checklist ? "체크리스트" : "자율 탐색"}</td>
                <td className="px-4 py-2">{r.autonomy}</td>
                <td className="px-4 py-2">{r.findings_count}</td>
                <td className="px-4 py-2 text-neutral-500">{new Date(r.created_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {runs.length === 0 && <p className="mt-4 text-sm text-neutral-500">실행이 없음</p>}
    </div>
  );
}

export default function RunsPage() {
  return (
    <AuthGate>
      <Header />
      <RunsTable />
    </AuthGate>
  );
}
