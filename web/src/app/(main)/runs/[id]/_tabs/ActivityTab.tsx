"use client";

import { useState } from "react";

import { EvidenceViewer } from "@/components/FindingPanel";
import { StatusBadge } from "@/components/StatusBadge";
import { Banner, Card, inputCls, SkeletonRows } from "@/components/ui";
import { ActivityEntry } from "@/lib/api";
import { formatTime } from "@/lib/labels";
import { useRunResource } from "@/lib/useRunResource";

// ── 실행 기록 ──
export function ActivityTab({ runId }: { runId: string }) {
  const { data: items, error } = useRunResource<ActivityEntry[]>(`/api/runs/${runId}/activity`, 4000);
  const [q, setQ] = useState("");
  const [onlyProblems, setOnlyProblems] = useState(false);

  if (error && !items) return <Banner>{error}</Banner>;
  if (!items) return <SkeletonRows />;
  if (items.length === 0) return <p className="p-4 text-sm text-faint">아직 실행된 도구 호출이 없습니다</p>;

  const needle = q.trim().toLowerCase();
  const rows = items
    .filter((t) => !onlyProblems || t.status === "FAILED" || t.status === "BLOCKED" || t.error)
    .filter((t) => !needle || t.tool.toLowerCase().includes(needle) || JSON.stringify(t.args ?? "").toLowerCase().includes(needle) || (t.asset_value ?? "").toLowerCase().includes(needle));

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="도구·명령·대상 검색" aria-label="실행 기록 검색" className={`${inputCls} !w-60`} />
        <label className="flex items-center gap-2">
          <input type="checkbox" className="accent-[var(--accent)]" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} />
          실패·차단만
        </label>
        <span className="ml-auto text-xs text-faint tabular">{rows.length} / {items.length}건</span>
      </div>
      <Card className="divide-y divide-line">
        {rows.length === 0 && <p className="p-4 text-sm text-faint">조건에 맞는 기록이 없습니다.</p>}
        {rows.map((t) => (
          <div key={t.id} className="p-3 text-xs">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="tabular text-faint">{formatTime(t.started_at)}</span>
              <span className="tabular text-muted">#{t.step_no}</span>
              <span className="font-mono text-sm font-semibold text-ink">{t.tool}</span>
              <StatusBadge value={t.status} />
              <span className="text-faint">정책: {t.policy_decision}</span>
              {t.asset_kind && <span className="font-mono text-muted">{t.asset_kind}:{t.asset_value}</span>}
            </div>
            {t.args != null && <div className="mt-1 break-all font-mono text-muted">명령 인자: {JSON.stringify(t.args)}</div>}
            {t.policy_reason && <div className="mt-0.5 text-faint">정책 사유: {t.policy_reason}</div>}
            {t.error && <div className="mt-0.5 text-danger">오류: {t.error}</div>}
            {t.evidence_id && (
              <div className="mt-1">
                <EvidenceViewer runId={runId} evidenceId={t.evidence_id} mediaType={t.evidence_media_type} size={t.evidence_size} />
              </div>
            )}
          </div>
        ))}
      </Card>
    </div>
  );
}
