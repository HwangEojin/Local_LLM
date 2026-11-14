"use client";

import { Banner, Card, Empty, SkeletonRows } from "@/components/ui";
import { useAssets } from "../_shared";

// ── 자산 ──
export function AssetsTab({ runId }: { runId: string }) {
  const { data, error } = useAssets(runId);

  if (error && !data) return <Banner>{error}</Banner>;
  if (!data) return <SkeletonRows />;
  if (data.assets.length === 0) return <Empty title="등록된 자산이 없습니다" />;

  return (
    <div className="space-y-4">
      <Card className="divide-y divide-line">
        {data.assets.map((a) => (
          <div key={a.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 p-3 text-xs">
            <span className="tabular text-faint">{a.target_id}</span>
            <span className="font-mono text-sm font-semibold text-ink">{a.kind}:{a.value}</span>
            {a.company && <span className="rounded bg-[var(--accent)]/10 px-1.5 py-0.5 text-[var(--accent)]">{a.company}</span>}
            <span className="text-faint">{a.verification}</span>
            <span className="text-faint">출처: {a.source}</span>
          </div>
        ))}
      </Card>
      {data.edges.length > 0 && (
        <Card className="divide-y divide-line">
          {data.edges.map((e, i) => (
            <div key={i} className="p-2 font-mono text-xs text-muted">
              {e.src} <span className="text-faint">-{e.rel}-&gt;</span> {e.dst}
            </div>
          ))}
        </Card>
      )}
    </div>
  );
}
