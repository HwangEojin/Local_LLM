"use client";

import { useMemo } from "react";

import { Banner, Card, Empty, SkeletonRows } from "@/components/ui";
import { useAssets } from "../_shared";

// ── 탐색 그래프 ──
export function GraphTab({ runId }: { runId: string }) {
  const { data, error } = useAssets(runId);

  // data 참조가 폴링마다 바뀌지 않는 한(usePolling 은 실제로 바뀐 응답만 setData 한다) 재계산하지 않는다.
  const graph = useMemo(() => {
    const assets = data?.assets ?? [], edges = data?.edges ?? [];
    const labels = Array.from(new Set([
      ...assets.map((a) => `${a.kind}:${a.value}`),
      ...edges.flatMap((e) => [e.src, e.dst]),
    ]));
    const kindByLabel = new Map(assets.map((a) => [`${a.kind}:${a.value}`, a.kind]));
    const size = Math.max(420, Math.min(900, 140 + labels.length * 36));
    const cx = size / 2, cy = size / 2, radius = size / 2 - 70;
    const pos = new Map(
      labels.map((label, i) => {
        const angle = (i / labels.length) * 2 * Math.PI;
        return [label, { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) }] as const;
      }),
    );
    return { labels, kindByLabel, pos, size };
  }, [data]);

  if (error && !data) return <Banner>{error}</Banner>;
  if (!data) return <SkeletonRows />;
  if (graph.labels.length === 0) return <Empty title="그릴 자산 관계가 없습니다" />;

  const { labels, kindByLabel, pos, size } = graph;
  const kindOf = (label: string) => kindByLabel.get(label) ?? label.split(":")[0] ?? "asset";
  const KIND_COLOR: Record<string, string> = {
    host: "var(--accent)", url: "#7c3aed", service: "#0ea5e9", path: "#f59e0b",
  };

  return (
    <Card className="overflow-auto p-4">
      <svg width={size} height={size} className="mx-auto" role="img" aria-label="자산·탐색 관계 그래프">
        {data.edges.map((e, i) => {
          const s = pos.get(e.src), d = pos.get(e.dst);
          if (!s || !d) return null;
          return (
            <g key={i}>
              <line x1={s.x} y1={s.y} x2={d.x} y2={d.y} stroke="var(--line)" strokeWidth={1.5} />
              <text x={(s.x + d.x) / 2} y={(s.y + d.y) / 2} fontSize={10} fill="var(--muted)" textAnchor="middle">
                {e.rel}
              </text>
            </g>
          );
        })}
        {labels.map((label) => {
          const p = pos.get(label);
          if (!p) return null;
          const kind = kindOf(label);
          return (
            <g key={label}>
              <circle cx={p.x} cy={p.y} r={7} fill={KIND_COLOR[kind] ?? "var(--muted)"} />
              <text x={p.x} y={p.y - 11} fontSize={11} fill="var(--ink)" textAnchor="middle" className="font-mono">
                {label.length > 28 ? `${label.slice(0, 26)}…` : label}
              </text>
            </g>
          );
        })}
      </svg>
    </Card>
  );
}
