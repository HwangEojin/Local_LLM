"use client";

// 자산 상세(G5-b, UPGRADE_PLAN.md) — 양방향 관계, 이 자산을 관찰한 실행 이력, 중복 제외 finding 목록 +
// 자산 중심 1-hop 그래프(RunDetail 탐색 그래프 탭의 AssetGraphSvg 재사용). 새 진단 로직 없음(읽기 전용).
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AssetGraphSvg } from "@/components/AssetGraph";
import { StatusBadge } from "@/components/StatusBadge";
import { Banner, Card, Empty, SkeletonRows } from "@/components/ui";
import { AssetDetail, api } from "@/lib/api";
import { ASSET_VERIFICATION_LABEL, formatTime } from "@/lib/labels";
import { usePolling } from "@/lib/usePolling";

export default function AssetDetailPage() {
  const params = useParams<{ id: string }>();
  const [data, setData] = useState<AssetDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get<AssetDetail>(`/api/assets/${params.id}`).then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  }, [params.id]);
  useEffect(load, [load]);
  usePolling(load, 20000);

  const centerLabel = data ? `${data.asset.kind}:${data.asset.value}` : "";
  const graphEdges = useMemo(
    () =>
      (data?.edges ?? []).map((e) => ({
        src: e.direction === "out" ? centerLabel : `${e.other_kind}:${e.other_value}`,
        rel: e.rel,
        dst: e.direction === "out" ? `${e.other_kind}:${e.other_value}` : centerLabel,
      })),
    [data, centerLabel],
  );
  const kindByLabel = useMemo(() => {
    const m = new Map<string, string>();
    if (data) {
      m.set(centerLabel, data.asset.kind);
      for (const e of data.edges) m.set(`${e.other_kind}:${e.other_value}`, e.other_kind);
    }
    return m;
  }, [data, centerLabel]);

  if (error && !data) return <main className="mx-auto w-full max-w-5xl p-6"><Banner>{error}</Banner></main>;
  if (!data) return <main className="mx-auto w-full max-w-5xl p-6"><SkeletonRows rows={4} /></main>;

  const { asset, edges, runs, findings } = data;

  return (
    <main className="mx-auto w-full max-w-5xl space-y-4 p-6">
      <div>
        <Link href="/assets" className="text-xs text-accent hover:underline">← 자산 목록</Link>
        <h1 className="mt-1 font-mono text-lg font-semibold">{asset.value}</h1>
        <p className="text-sm text-muted">
          {asset.kind} · {ASSET_VERIFICATION_LABEL[asset.verification] ?? asset.verification}
          {asset.company && <> · {asset.company}</>} · 출처 {asset.source}
        </p>
      </div>

      <section>
        <h2 className="mb-2 text-sm font-semibold">관계</h2>
        <AssetGraphSvg edges={graphEdges} extraLabels={[centerLabel]} kindOf={(l) => kindByLabel.get(l) ?? l.split(":")[0] ?? "asset"} />
        {edges.length > 0 && (
          <Card className="mt-2 divide-y divide-line">
            {edges.map((e) => (
              <div key={e.id + e.direction} className="flex items-center gap-2 p-2 text-xs">
                <span className="text-faint">{e.direction === "out" ? "→" : "←"}</span>
                <span className="font-medium">{e.rel}</span>
                <Link href={`/assets/${e.other_id}`} className="font-mono text-accent hover:underline">
                  {e.other_kind}:{e.other_value}
                </Link>
              </div>
            ))}
          </Card>
        )}
      </section>

      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold">관찰한 실행 ({runs.length})</h2>
          {runs.length >= 2 && (
            <Link
              href={`/runs/compare?base=${encodeURIComponent(runs[1].run_code)}&head=${encodeURIComponent(runs[0].run_code)}`}
              className="text-xs text-accent hover:underline"
            >
              이 자산의 실행 비교 →
            </Link>
          )}
        </div>
        {runs.length === 0 ? (
          <Empty title="이 자산을 관찰한 실행이 없습니다" />
        ) : (
          <Card className="divide-y divide-line">
            {runs.map((r) => (
              <div key={r.id} className="flex items-center justify-between gap-2 p-2 text-sm">
                <Link href={`/runs/${r.run_code}`} className="font-mono text-xs text-accent hover:underline">
                  {r.run_code}
                </Link>
                <StatusBadge value={r.status} />
                <span className="text-xs text-faint">{formatTime(r.created_at)}</span>
              </div>
            ))}
          </Card>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold">발견 사항(중복 제외, {findings.length})</h2>
        {findings.length === 0 ? (
          <Empty title="이 자산에 발견 사항이 없습니다" />
        ) : (
          <Card className="divide-y divide-line">
            {findings.map((f) => (
              <div key={f.id} className="flex items-center justify-between gap-2 p-2 text-sm">
                <span className="font-medium">{f.title}</span>
                <div className="flex items-center gap-2">
                  <Link href={`/runs/${f.run_code}`} className="font-mono text-xs text-accent hover:underline">
                    {f.run_code}
                  </Link>
                  <StatusBadge value={f.status} />
                </div>
              </div>
            ))}
          </Card>
        )}
      </section>
    </main>
  );
}
