"use client";

// 발견 사항 전역 화면(UPGRADE_PLAN.md B2) — 진단 상세(runs/[id])의 '발견 사항' 탭에 묻혀 있던 걸 전체 진단
// 범위로 승격한다. 새 진단 로직 없음: /api/findings(findings_global.py, findings 를 runs 와 조인)만 읽는다.
// 상세/검토/재검증은 기존 FindingPanel(run 범위 엔드포인트)을 각 행의 run_id 로 그대로 재사용한다.
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";

import { FindingPanel } from "@/components/FindingPanel";
import { StatusBadge } from "@/components/StatusBadge";
import { Banner, btnGhost, Card, Empty, inputCls, SkeletonRows } from "@/components/ui";
import { api, FINDING_STATUSES, FindingWithRun } from "@/lib/api";
import { describe, formatTime } from "@/lib/labels";
import { usePolling } from "@/lib/usePolling";

const PAGE = 50;
const th = "px-3 py-1.5 text-xs font-medium";
const chip = (on: boolean) =>
  `rounded-full border px-2.5 py-1 text-xs transition-colors ${on ? "border-accent bg-accent/10 text-accent" : "border-line text-muted hover:text-ink"}`;

export default function FindingsPage() {
  const [items, setItems] = useState<FindingWithRun[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const load = useCallback(() => {
    const p = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
    if (q.trim()) p.set("q", q.trim());
    if (status) p.set("status", status);
    api
      .get<{ items: FindingWithRun[]; total: number }>(`/api/findings?${p.toString()}`)
      .then((r) => { setItems(r.items); setTotal(r.total); setError(null); })
      .catch((e) => setError(e.message));
  }, [q, status, offset]);
  useEffect(load, [load]);
  usePolling(load, 15000);

  const filtering = q !== "" || status !== "";
  const page = Math.floor(offset / PAGE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const sorted = useMemo(
    () => items?.slice().sort((a, b) => b.created_at.localeCompare(a.created_at)) ?? null,
    [items],
  );
  const selected = sorted?.find((f) => f.id === selectedId) ?? null;

  return (
    <main className="mx-auto w-full max-w-6xl space-y-4 p-6">
      <h1 className="text-lg font-semibold">발견 사항</h1>
      <p className="text-sm text-muted">모든 진단의 발견 사항을 가로질러 검색합니다.</p>
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}

      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" aria-hidden />
            <input
              value={q}
              onChange={(e) => { setQ(e.target.value); setOffset(0); }}
              placeholder="제목·근거 검색"
              aria-label="발견 사항 검색"
              className={`${inputCls} !w-60 pl-8`}
            />
          </div>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="판정 상태 필터">
            {FINDING_STATUSES.map((s) => (
              <button key={s} aria-pressed={status === s} onClick={() => { setStatus(status === s ? "" : s); setOffset(0); }} className={chip(status === s)}>
                {describe(s).label}
              </button>
            ))}
          </div>
          {filtering && (
            <button onClick={() => { setQ(""); setStatus(""); setOffset(0); }} className={btnGhost}>
              필터 초기화
            </button>
          )}
          <span className="ml-auto text-xs text-faint tabular">전체 {total}건</span>
        </div>
      </div>

      {!sorted ? (
        <SkeletonRows rows={6} />
      ) : sorted.length === 0 ? (
        <Empty title="조건에 맞는 발견 사항이 없습니다" hint={filtering ? "필터를 줄여 보세요." : "진단이 진행되면 취약점 후보가 여기에 쌓입니다."} />
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-raised text-left text-muted">
              <tr>
                <th scope="col" className={th}>제목</th>
                <th scope="col" className={th}>진단</th>
                <th scope="col" className={th}>판정</th>
                <th scope="col" className={th}>검토자</th>
                <th scope="col" className={th}>발견 시각</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((f) => (
                <tr
                  key={f.id}
                  tabIndex={0}
                  aria-selected={selected?.id === f.id}
                  onClick={() => setSelectedId(f.id)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelectedId(f.id); } }}
                  className={`cursor-pointer border-t border-line transition-colors hover:bg-raised/60 ${selected?.id === f.id ? "bg-raised" : ""}`}
                >
                  <td className="px-3 py-1.5 font-medium">{f.title}</td>
                  <td className="px-3 py-1.5">
                    <Link href={`/runs/${f.run_code}`} onClick={(e) => e.stopPropagation()} className="font-mono text-xs text-accent hover:underline">
                      {f.run_code}
                    </Link>
                  </td>
                  <td className="px-3 py-1.5"><StatusBadge value={f.status} /></td>
                  <td className="px-3 py-1.5 text-muted">{f.reviewed_by ?? "검토 전"}</td>
                  <td className="px-3 py-1.5 text-muted">{formatTime(f.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {pages > 1 && (
        <div className="flex items-center justify-center gap-3 text-sm text-muted">
          <button disabled={page <= 1} onClick={() => setOffset(Math.max(0, offset - PAGE))} className={btnGhost}>
            <ChevronLeft className="h-4 w-4" aria-hidden /> 이전
          </button>
          <span className="tabular">{page} / {pages}</span>
          <button disabled={page >= pages} onClick={() => setOffset(offset + PAGE)} className={btnGhost}>
            다음 <ChevronRight className="h-4 w-4" aria-hidden />
          </button>
        </div>
      )}

      {selected && (
        <FindingPanel
          key={selected.id}
          runId={selected.run_id}
          finding={selected}
          onClose={() => setSelectedId(null)}
          onChanged={load}
        />
      )}
    </main>
  );
}
