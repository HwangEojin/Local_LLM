"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { StatusBadge } from "@/components/StatusBadge";
import { Banner, Card, Empty, SkeletonRows } from "@/components/ui";
import { api, AuditEvent, DashboardData, FindingWithRun } from "@/lib/api";
import { describe, formatTime, SEVERITY_ORDER } from "@/lib/labels";
import { usePolling } from "@/lib/usePolling";

const TOTAL_LABEL: Record<keyof DashboardData["totals"], string> = {
  runs: "진단 실행", findings: "발견 사항", evidence: "증적", assets: "자산",
};

function Bar({ label, n, max, tone }: { label: string; n: number; max: number; tone: string }) {
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="w-28 shrink-0 text-muted">{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-raised">
        <div className="h-full rounded-full" style={{ width: `${max ? (n / max) * 100 : 0}%`, background: tone }} />
      </div>
      <span className="w-8 shrink-0 text-right tabular text-faint">{n}</span>
    </div>
  );
}

function TokenChart({ daily, totals }: { daily: DashboardData["token_daily"]; totals: DashboardData["token_totals"] }) {
  if (totals.calls === 0) return <Empty title="LLM 호출 기록이 없습니다" />;
  const max = Math.max(1, ...daily.map((d) => d.tokens));
  return (
    <div className="space-y-3">
      <div className="flex items-end gap-1" style={{ height: 72 }}>
        {daily.map((d) => (
          <div key={d.day} className="flex-1" title={`${d.day}: 토큰 ${d.tokens.toLocaleString()} · 호출 ${d.calls}건`}>
            <div className="rounded-sm bg-accent" style={{ height: `${Math.max(2, (d.tokens / max) * 72)}px` }} />
          </div>
        ))}
        {daily.length === 0 && <span className="text-xs text-faint">최근 14일간 기록 없음</span>}
      </div>
      <p className="text-xs text-faint">
        최근 14일 누적 · 호출 {totals.calls.toLocaleString()}건 · 토큰 {(totals.tokens ?? 0).toLocaleString()}
        {totals.failed > 0 && <span className="text-danger"> · 실패 {totals.failed}건</span>}
      </p>
    </div>
  );
}

function ActivityFeed({ items }: { items: AuditEvent[] | null }) {
  if (!items) return <SkeletonRows rows={3} />;
  if (items.length === 0) return <Empty title="최근 활동이 없습니다" />;
  return (
    <ul className="divide-y divide-line text-sm">
      {items.map((e) => (
        <li key={e.id} className="flex items-center gap-3 px-1 py-2">
          <span className="font-mono text-xs text-muted">{e.event}</span>
          {e.run_code && <span className="font-mono text-xs text-accent">{e.run_code}</span>}
          <span className="ml-auto shrink-0 text-xs text-faint">{formatTime(e.ts)}</span>
        </li>
      ))}
    </ul>
  );
}

function NeedsReviewPanel({ items }: { items: FindingWithRun[] | null }) {
  if (!items) return <SkeletonRows rows={3} />;
  if (items.length === 0) return <Empty title="검토 대기 중인 발견 사항이 없습니다" />;
  return (
    <ul className="divide-y divide-line text-sm">
      {items.map((f) => (
        <li key={f.id}>
          <Link href="/findings" className="flex items-center gap-3 px-1 py-2 hover:bg-raised">
            <span className="truncate">{f.title}</span>
            <StatusBadge value={f.severity} />
            <span className="ml-auto shrink-0 font-mono text-xs text-faint">{f.run_code}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function DashboardBody() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activity, setActivity] = useState<AuditEvent[] | null>(null);
  const [needsReview, setNeedsReview] = useState<FindingWithRun[] | null>(null);

  const load = useCallback(() => {
    api.get<DashboardData>("/api/dashboard").then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
    api.get<AuditEvent[]>("/api/admin/audit?limit=8").then(setActivity).catch(() => {});
    api
      .get<{ items: FindingWithRun[] }>("/api/findings?status=NEEDS_REVIEW&limit=5")
      .then((r) => setNeedsReview(r.items))
      .catch(() => {});
  }, []);
  useEffect(load, [load]);
  usePolling(load, 15000);

  if (error && !data) return <main className="mx-auto w-full max-w-6xl p-6"><Banner>{error}</Banner></main>;
  if (!data) return <main className="mx-auto w-full max-w-6xl p-6"><SkeletonRows rows={4} /></main>;

  const statusMax = Math.max(1, ...data.by_status.map((r) => r.n));
  const severityMax = Math.max(1, ...data.by_severity.map((r) => r.n));
  const severityByKey = Object.fromEntries(data.by_severity.map((r) => [r.severity, r.n]));

  return (
    <main className="mx-auto w-full max-w-6xl space-y-6 p-6">
      <h1 className="text-lg font-semibold text-ink">대시보드</h1>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {(Object.keys(TOTAL_LABEL) as (keyof DashboardData["totals"])[]).map((k) => (
          <Card key={k} className="p-4">
            <p className="text-xs text-faint">{TOTAL_LABEL[k]}</p>
            <p className="mt-1 text-2xl font-semibold tabular text-ink">{data.totals[k]}</p>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card className="space-y-3 p-4">
          <h2 className="text-sm font-medium text-ink">실행 상태별</h2>
          {data.by_status.length === 0 ? (
            <Empty title="실행 기록이 없습니다" />
          ) : (
            data.by_status.map((r) => (
              <Bar key={r.status} label={describe(r.status).label} n={r.n} max={statusMax} tone="var(--accent)" />
            ))
          )}
        </Card>
        <Card className="space-y-3 p-4">
          <h2 className="text-sm font-medium text-ink">확정 발견 사항(심각도별)</h2>
          {data.by_severity.length === 0 ? (
            <Empty title="확정된 발견 사항이 없습니다" />
          ) : (
            SEVERITY_ORDER.filter((s) => severityByKey[s]).map((s) => (
              <Bar key={s} label={describe(s).label} n={severityByKey[s] ?? 0} max={severityMax} tone="var(--danger)" />
            ))
          )}
        </Card>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card className="space-y-3 p-4">
          <h2 className="text-sm font-medium text-ink">LLM 토큰 사용량</h2>
          <TokenChart daily={data.token_daily} totals={data.token_totals} />
        </Card>
        <Card className="space-y-2 p-4">
          <h2 className="text-sm font-medium text-ink">검토 대기</h2>
          <NeedsReviewPanel items={needsReview} />
        </Card>
      </div>

      <Card className="space-y-2 p-4">
        <h2 className="text-sm font-medium text-ink">최근 활동</h2>
        <ActivityFeed items={activity} />
      </Card>

      <Card className="divide-y divide-line">
        <div className="p-3 text-sm font-medium text-ink">최근 진단</div>
        {data.recent_runs.length === 0 ? (
          <Empty title="아직 진단 실행이 없습니다" />
        ) : (
          data.recent_runs.map((r) => (
            <Link key={r.id} href={`/runs/${r.id}`} className="flex items-center gap-3 p-3 text-sm hover:bg-raised">
              <span className="font-mono text-ink">{r.run_code}</span>
              <StatusBadge value={r.status} />
              <span className="text-faint">{r.operator}</span>
              <span className="ml-auto tabular text-faint">발견 {r.findings_count}건</span>
              <span className="tabular text-xs text-faint">{formatTime(r.created_at)}</span>
            </Link>
          ))
        )}
      </Card>
    </main>
  );
}

export default function DashboardPage() {
  return <DashboardBody />;
}
