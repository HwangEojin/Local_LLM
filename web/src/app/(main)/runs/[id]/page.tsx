"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { FindingPanel } from "@/components/FindingPanel";
import { Banner, SkeletonRows } from "@/components/ui";
import { api, BacklogItem, Finding, RunDetail } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";

import { DeletedBanner } from "./_parts/DeletedBanner";
import { Summary } from "./_parts/Summary";
import { FindingFilter, needsApproval, NO_FILTER, TabKey, TAB_KEYS, TAB_LABEL } from "./_shared";
import { ActivityTab } from "./_tabs/ActivityTab";
import { AssetsTab } from "./_tabs/AssetsTab";
import { BacklogTab } from "./_tabs/BacklogTab";
import { ChatTab } from "./_tabs/ChatTab";
import { EvidenceTab } from "./_tabs/EvidenceTab";
import { FindingsTab } from "./_tabs/FindingsTab";
import { GraphTab } from "./_tabs/GraphTab";
import { ReportTab } from "./_tabs/ReportTab";
import { ServerLogTab } from "./_tabs/ServerLogTab";
import { TrafficTab } from "./_tabs/TrafficTab";
import { TriggersTab } from "./_tabs/TriggersTab";

// ── 본문 ──
function RunDetailBody({ runId }: { runId: string }) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [backlog, setBacklog] = useState<BacklogItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 이 컴포넌트는 AuthGate 통과 후(클라이언트에서만) 렌더되므로 lazy 초기화에서 window 를 읽어도 hydration 불일치가 없다.
  const [tab, setTabState] = useState<TabKey>(() => {
    const h = window.location.hash.slice(1) as TabKey;
    return TAB_KEYS.includes(h) ? h : "findings";
  });
  const [filter, setFilter] = useState<FindingFilter>(NO_FILTER);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // 탭을 URL 해시에 보관해 새로고침·링크 공유 후에도 같은 화면으로 돌아오게 한다.
  const setTab = useCallback((t: TabKey) => {
    setTabState(t);
    window.history.replaceState(null, "", `#${t}`);
  }, []);

  const load = useCallback(() => {
    api.get<RunDetail>(`/api/runs/${runId}`).then((d) => { setDetail(d); setError(null); }).catch((e) => setError(e.message));
    api.get<Finding[]>(`/api/runs/${runId}/findings`).then(setFindings).catch(() => {});
    api.get<BacklogItem[]>(`/api/runs/${runId}/backlog`).then(setBacklog).catch(() => {});
  }, [runId]);

  useEffect(load, [load]);
  // 실행 중이면 4초, 아니면 15초. 탭이 숨겨져 있으면 멈춘다.
  usePolling(load, detail?.loop_running ? 4000 : 15000);

  const selected = findings?.find((f) => f.id === selectedId) ?? null;
  const closePanel = useCallback(() => setSelectedId(null), []);

  if (error && !detail) return <main className="mx-auto w-full max-w-6xl p-6"><Banner>{error}</Banner></main>;
  if (!detail) return <main className="mx-auto w-full max-w-6xl p-6"><SkeletonRows rows={4} /></main>;

  const approvals = (backlog ?? []).filter(needsApproval).length;
  const counts: Partial<Record<TabKey, number>> = {
    findings: findings?.length,
    backlog: backlog?.length,
    evidence: detail.usage.evidence,
  };

  return (
    <main className="mx-auto w-full max-w-6xl p-6">
      {error && <Banner tone="warn">최신 상태를 가져오지 못했습니다. 화면은 마지막으로 받은 값입니다. ({error})</Banner>}
      <DeletedBanner detail={detail} onChanged={load} />
      <Summary
        detail={detail}
        findings={findings}
        backlog={backlog}
        onFilterFindings={setFilter}
        onShowTab={setTab}
        onChanged={load}
      />

      <div role="tablist" aria-label="진단 상세" className="mb-4 flex gap-1 border-b border-line">
        {TAB_KEYS.map((t) => (
          <button
            key={t}
            role="tab"
            id={`tab-${t}`}
            aria-selected={tab === t}
            aria-controls="tabpanel"
            onClick={() => setTab(t)}
            className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition-colors ${
              tab === t ? "border-accent font-medium text-ink" : "border-transparent text-muted hover:text-ink"
            }`}
          >
            {TAB_LABEL[t]}
            {counts[t] != null && <span className="tabular text-xs text-faint">{counts[t]}</span>}
            {t === "backlog" && approvals > 0 && (
              <span className="rounded-full bg-warn px-1.5 text-[11px] font-semibold text-canvas" title="승인 대기">
                {approvals}
              </span>
            )}
          </button>
        ))}
      </div>

      <div role="tabpanel" id="tabpanel" aria-labelledby={`tab-${tab}`}>
        {tab === "findings" && (
          <FindingsTab runId={runId} findings={findings} filter={filter} setFilter={setFilter} selectedId={selectedId} onSelect={setSelectedId} />
        )}
        {tab === "backlog" && <BacklogTab runId={runId} items={backlog} onChanged={load} />}
        {tab === "assets" && <AssetsTab runId={runId} />}
        {tab === "graph" && <GraphTab runId={runId} />}
        {tab === "traffic" && <TrafficTab runId={runId} />}
        {tab === "chat" && <ChatTab runId={runId} />}
        {tab === "evidence" && <EvidenceTab runId={runId} />}
        {tab === "activity" && <ActivityTab runId={runId} />}
        {tab === "server-log" && <ServerLogTab runId={runId} />}
        {tab === "triggers" && <TriggersTab runId={runId} />}
        {tab === "report" && <ReportTab runId={runId} completed={detail.run.status === "COMPLETED"} />}
      </div>

      {selected && <FindingPanel key={selected.id} runId={runId} finding={selected} onClose={closePanel} onChanged={load} />}
    </main>
  );
}

export default function RunDetailPage() {
  const params = useParams<{ id: string }>();
  return <RunDetailBody runId={params.id} />;
}
