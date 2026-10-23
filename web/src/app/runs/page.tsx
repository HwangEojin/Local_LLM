"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { AuthGate } from "@/components/AuthGate";
import { Header } from "@/components/Header";
import { StatusBadge } from "@/components/StatusBadge";
import { api, RUN_STATUSES, RunList } from "@/lib/api";

const PAGE_SIZE = 20;

function RunsTable() {
  const [data, setData] = useState<RunList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [qInput, setQInput] = useState(""); // 입력 중인 값(디바운스 전) — 검색창이 매 글자마다 다시 쿼리하지 않게 분리
  const [statuses, setStatuses] = useState<string[]>([]);
  const [sort, setSort] = useState<"created_at" | "status" | "operator">("created_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    const id = setTimeout(() => {
      setQ(qInput);
      setOffset(0);
    }, 300); // 검색어 입력 디바운스
    return () => clearTimeout(id);
  }, [qInput]);

  useEffect(() => {
    const params = new URLSearchParams({
      sort,
      order,
      limit: String(PAGE_SIZE),
      offset: String(offset),
    });
    if (q.trim()) params.set("q", q.trim());
    if (statuses.length) params.set("status", statuses.join(","));
    api
      .get<RunList>(`/api/runs?${params.toString()}`)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [q, statuses, sort, order, offset]);

  function toggleStatus(s: string) {
    setOffset(0);
    setStatuses((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]));
  }

  function toggleSort(col: "created_at" | "status" | "operator") {
    setOffset(0);
    if (sort === col) {
      setOrder((o) => (o === "asc" ? "desc" : "asc"));
    } else {
      setSort(col);
      setOrder("desc");
    }
  }

  const sortArrow = (col: string) => (sort === col ? (order === "asc" ? " ▲" : " ▼") : "");

  if (error) return <p className="p-6 text-sm text-red-600">{error}</p>;

  const runs = data?.runs ?? null;
  const total = data?.total ?? 0;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">실행(Run) 목록</h1>
        <Link
          href="/runs/new"
          className="rounded bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-neutral-700"
        >
          + 새 진단
        </Link>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-3">
        <input
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
          placeholder="실행 ID 또는 운영자 검색"
          className="w-64 rounded border border-neutral-300 px-3 py-1.5 text-sm"
        />
        <div className="flex flex-wrap gap-1.5">
          {RUN_STATUSES.map((s) => (
            <button
              key={s}
              onClick={() => toggleStatus(s)}
              className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
                statuses.includes(s)
                  ? "border-neutral-900 bg-neutral-900 text-white"
                  : "border-neutral-300 text-neutral-600 hover:border-neutral-400"
              }`}
            >
              {s}
            </button>
          ))}
          {statuses.length > 0 && (
            <button
              onClick={() => {
                setStatuses([]);
                setOffset(0);
              }}
              className="text-xs text-neutral-500 hover:underline"
            >
              필터 초기화
            </button>
          )}
        </div>
      </div>

      {!runs ? (
        <p className="text-sm text-neutral-500">불러오는 중...</p>
      ) : (
        <>
          <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-neutral-500">
                <tr>
                  <th className="px-4 py-2 font-medium">ID</th>
                  <th className="cursor-pointer select-none px-4 py-2 font-medium" onClick={() => toggleSort("status")}>
                    상태{sortArrow("status")}
                  </th>
                  <th
                    className="cursor-pointer select-none px-4 py-2 font-medium"
                    onClick={() => toggleSort("operator")}
                  >
                    운영자{sortArrow("operator")}
                  </th>
                  <th className="px-4 py-2 font-medium">모드</th>
                  <th className="px-4 py-2 font-medium">자율성</th>
                  <th className="px-4 py-2 font-medium">발견 사항</th>
                  <th
                    className="cursor-pointer select-none px-4 py-2 font-medium"
                    onClick={() => toggleSort("created_at")}
                  >
                    생성 시각{sortArrow("created_at")}
                  </th>
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
          {runs.length === 0 && <p className="mt-4 text-sm text-neutral-500">조건에 맞는 실행이 없음</p>}

          {total > 0 && (
            <div className="mt-3 flex items-center justify-between text-sm text-neutral-500">
              <span>
                전체 {total}건 중 {offset + 1}–{Math.min(offset + PAGE_SIZE, total)}
              </span>
              <div className="flex items-center gap-2">
                <button
                  disabled={page <= 1}
                  onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                  className="rounded border border-neutral-300 px-2 py-1 disabled:opacity-40"
                >
                  이전
                </button>
                <span>
                  {page} / {pageCount}
                </span>
                <button
                  disabled={page >= pageCount}
                  onClick={() => setOffset((o) => o + PAGE_SIZE)}
                  className="rounded border border-neutral-300 px-2 py-1 disabled:opacity-40"
                >
                  다음
                </button>
              </div>
            </div>
          )}
        </>
      )}
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
