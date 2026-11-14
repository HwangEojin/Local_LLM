"use client";

import { useMemo } from "react";

import { Card, Empty } from "@/components/ui";

const KIND_COLOR: Record<string, string> = {
  host: "var(--accent)", url: "#7c3aed", service: "#0ea5e9", path: "#f59e0b",
};

/** 노드·관계 그래프를 원형 레이아웃으로 그린다(RunDetail 의 탐색 그래프 탭과 자산 상세의 1-hop 그래프가 공유).
 * G5-b, UPGRADE_PLAN.md "기존 GraphTab 그래프 컴포넌트를 재사용". */
export function AssetGraphSvg({
  edges,
  kindOf,
  extraLabels = [],
}: {
  edges: { src: string; rel: string; dst: string }[];
  kindOf: (label: string) => string;
  extraLabels?: string[]; // 관계가 없어도 보여 줄 노드(예: 간선 없는 자산, 중심 자산)
}) {
  const graph = useMemo(() => {
    const labels = Array.from(new Set([...extraLabels, ...edges.flatMap((e) => [e.src, e.dst])]));
    const size = Math.max(420, Math.min(900, 140 + labels.length * 36));
    const cx = size / 2, cy = size / 2, radius = size / 2 - 70;
    const pos = new Map(
      labels.map((label, i) => {
        const angle = (i / labels.length) * 2 * Math.PI;
        return [label, { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) }] as const;
      }),
    );
    return { labels, pos, size };
  }, [edges, extraLabels]);

  if (graph.labels.length === 0) return <Empty title="그릴 자산 관계가 없습니다" />;

  const { labels, pos, size } = graph;
  return (
    <Card className="overflow-auto p-4">
      <svg width={size} height={size} className="mx-auto" role="img" aria-label="자산 관계 그래프">
        {edges.map((e, i) => {
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
