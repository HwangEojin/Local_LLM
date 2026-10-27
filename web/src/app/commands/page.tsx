"use client";

// 명령(도구 호출) 이력: 모든 진단을 가로지르는 조회 전용 화면(artex 의 commands 메뉴 참고). 진단 상세의
// '실행 기록' 탭과 같은 데이터(tool_invocations)를 전체 진단 범위로 검색한다. 새 진단 로직 없음.
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";

import { AuthGate } from "@/components/AuthGate";
import { EvidenceViewer } from "@/components/FindingPanel";
import { Header } from "@/components/Header";
import { Banner, Card, Empty, inputCls, SkeletonRows } from "@/components/ui";
import { StatusBadge } from "@/components/StatusBadge";
import { ActivityEntry, api } from "@/lib/api";
import { formatTime } from "@/lib/labels";

type Row = ActivityEntry & { run_code: string | null };
type ToolStat = { tool: string; calls: number; failures: number; last_at: string };

const PAGE = 50;

function useDebounced(value: string, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function CommandsBody() {
  const [q, setQ] = useState("");
  const [tool, setTool] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<ToolStat[] | null>(null);
  const debouncedQ = useDebounced(q);

  useEffect(() => {
    api.get<ToolStat[]>("/api/commands/stats").then(setStats).catch(() => setStats([]));
  }, []);

  const load = useCallback(() => {
    const p = new URLSearchParams({ q: debouncedQ, limit: String(PAGE), offset: String(offset) });
    if (tool) p.set("tool", tool);
    if (status) p.set("status", status);
    api
      .get<{ items: Row[]; total: number }>(`/api/commands?${p.toString()}`)
      .then((r) => {
        setRows(r.items);
        setTotal(r.total);
        setError(null);
      })
      .catch((e) => setError(e.message));
  }, [debouncedQ, tool, status, offset]);
  useEffect(load, [load]);
  useEffect(() => setOffset(0), [debouncedQ, tool, status]);

  const page = Math.floor(offset / PAGE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  return (
    <main className="mx-auto w-full max-w-6xl p-6">
      <h1 className="mb-1 text-lg font-semibold">명령 이력</h1>
      <p className="mb-5 text-sm text-muted">모든 진단의 도구 호출을 가로질러 검색합니다.</p>

      {stats && stats.length > 0 && (
        <Card className="mb-4 overflow-x-auto p-4">
          <h2 className="mb-2 text-sm font-semibold">도구별 호출</h2>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {stats.map((s) => (
              <button
                key={s.tool}
                onClick={() => setTool(tool === s.tool ? "" : s.tool)}
                className={`shrink-0 rounded-md border px-3 py-1.5 text-left text-xs transition-colors ${tool === s.tool ? "border-accent bg-accent/15" : "border-line hover:bg-raised"}`}
              >
                <div className="font-mono">{s.tool}</div>
                <div className="tabular text-faint">
                  호출 {s.calls}건{s.failures > 0 && <span className="text-danger"> · 실패 {s.failures}</span>}
                </div>
              </button>
            ))}
          </div>
        </Card>
      )}

      <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" aria-hidden />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="도구·인자·오류·사유 검색" aria-label="명령 검색" className={`${inputCls} !w-64 pl-8`} />
        </div>
        <select className={`${inputCls} !w-40`} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="상태 필터">
          <option value="">모든 상태</option>
          <option value="SUCCEEDED">성공</option>
          <option value="FAILED">실패</option>
          <option value="SKIPPED">건너뜀</option>
        </select>
        {tool && (
          <button onClick={() => setTool("")} className="text-xs text-accent hover:underline">
            도구 필터 해제({tool})
          </button>
        )}
        <span className="ml-auto text-xs text-faint tabular">전체 {total}건</span>
      </div>

      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      {!rows ? (
        <SkeletonRows />
      ) : rows.length === 0 ? (
        <Empty title="조건에 맞는 기록이 없습니다" />
      ) : (
        <Card className="divide-y divide-line">
          {rows.map((t) => (
            <div key={t.id} className="p-3 text-xs">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="tabular text-faint">{formatTime(t.started_at)}</span>
                {t.run_code && (
                  <Link href={`/runs/${t.run_code}`} className="font-mono text-accent hover:underline">
                    {t.run_code}
                  </Link>
                )}
                <span className="tabular text-muted">#{t.step_no}</span>
                <span className="font-mono text-sm font-semibold text-ink">{t.tool}</span>
                <StatusBadge value={t.status} />
                <span className="text-faint">정책: {t.policy_decision}</span>
                {t.asset_kind && <span className="font-mono text-muted">{t.asset_kind}:{t.asset_value}</span>}
              </div>
              {t.args != null && <div className="mt-1 break-all font-mono text-muted">명령 인자: {JSON.stringify(t.args)}</div>}
              {t.policy_reason && <div className="mt-0.5 text-faint">정책 사유: {t.policy_reason}</div>}
              {t.error && <div className="mt-0.5 text-danger">오류: {t.error}</div>}
              {t.evidence_id && t.run_id && (
                <div className="mt-1">
                  <EvidenceViewer runId={t.run_id} evidenceId={t.evidence_id} mediaType={t.evidence_media_type} size={t.evidence_size} />
                </div>
              )}
            </div>
          ))}
        </Card>
      )}

      {total > PAGE && (
        <div className="mt-4 flex items-center justify-between text-sm">
          <span className="text-faint">
            {offset + 1}–{Math.min(offset + PAGE, total)} / {total}건
          </span>
          <div className="flex items-center gap-2">
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} className="flex items-center gap-1 rounded-md border border-line px-2 py-1 disabled:opacity-40">
              <ChevronLeft className="h-4 w-4" aria-hidden /> 이전
            </button>
            <span className="tabular text-faint">{page} / {pages}</span>
            <button disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)} className="flex items-center gap-1 rounded-md border border-line px-2 py-1 disabled:opacity-40">
              다음 <ChevronRight className="h-4 w-4" aria-hidden />
            </button>
          </div>
        </div>
      )}
    </main>
  );
}

export default function CommandsPage() {
  return (
    <AuthGate>
      <Header />
      <CommandsBody />
    </AuthGate>
  );
}
