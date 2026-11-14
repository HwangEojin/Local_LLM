"use client";

import { useMemo } from "react";

import { AssetGraphSvg } from "@/components/AssetGraph";
import { Banner, SkeletonRows } from "@/components/ui";
import { useAssets } from "../_shared";

// ── 탐색 그래프 ──
export function GraphTab({ runId }: { runId: string }) {
  const { data, error } = useAssets(runId);

  const { kindByLabel, assetLabels } = useMemo(() => {
    const assets = data?.assets ?? [];
    return {
      kindByLabel: new Map(assets.map((a) => [`${a.kind}:${a.value}`, a.kind])),
      assetLabels: assets.map((a) => `${a.kind}:${a.value}`),
    };
  }, [data]);

  if (error && !data) return <Banner>{error}</Banner>;
  if (!data) return <SkeletonRows />;

  return (
    <AssetGraphSvg
      edges={data.edges}
      extraLabels={assetLabels}
      kindOf={(label) => kindByLabel.get(label) ?? label.split(":")[0] ?? "asset"}
    />
  );
}
