"use client";

// 트래픽 전역 화면(UPGRADE_PLAN.md B2) — 진단 상세(runs/[id])의 '트래픽' 탭에 묻혀 있던 걸 전체 진단 범위로
// 승격한다. 새 진단 로직 없음: /api/traffic(global_views.py, 자체 레코딩 프록시 evidence 를 runs 와 조인)만
// 읽고, 다운로드는 기존 /api/runs/{run}/evidence/{id}/download 를 각 행의 run_id 로 그대로 쓴다.
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";

import { Banner, btnGhost, Card, Empty, inputCls, SkeletonRows } from "@/components/ui";
import { api, downloadUrl, TrafficEntryWithRun } from "@/lib/api";
import { formatBytes, formatTime } from "@/lib/labels";
import { usePolling } from "@/lib/usePolling";

const PAGE = 50;
const th = "px-4 py-2 text-xs font-medium";

export default function TrafficPage() {
  const [items, setItems] = useState<TrafficEntryWithRun[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState("");
  const [offset, setOffset] = useState(0);

  const load = useCallback(() => {
    const p = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
    if (run.trim()) p.set("run", run.trim());
    api
      .get<{ items: TrafficEntryWithRun[]; total: number }>(`/api/traffic?${p.toString()}`)
      .then((r) => { setItems(r.items); setTotal(r.total); setError(null); })
      .catch((e) => setError(e.message));
  }, [run, offset]);
  useEffect(load, [load]);
  usePolling(load, 15000);

  const page = Math.floor(offset / PAGE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  return (
    <main className="mx-auto w-full max-w-6xl space-y-4 p-6">
      <h1 className="text-lg font-semibold">트래픽</h1>
      <p className="text-sm text-muted">자체 레코딩 프록시(workbench proxy)가 남긴 요청/응답을 모든 진단에서 가로질러 봅니다.</p>
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" aria-hidden />
          <input
            value={run}
            onChange={(e) => { setRun(e.target.value); setOffset(0); }}
            placeholder="진단 ID"
            aria-label="진단 ID로 필터"
            className={`${inputCls} !w-60 pl-8`}
          />
        </div>
        <span className="ml-auto text-xs text-faint tabular">전체 {total}건</span>
      </div>

      {!items ? (
        <SkeletonRows rows={6} />
      ) : items.length === 0 ? (
        <Empty title="기록된 트래픽이 없습니다" hint="workbench proxy 로 진단 대상의 프록시를 띄우고 브라우저/도구를 그쪽으로 돌리면 여기에 쌓입니다." />
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-faint">
              <tr>
                <th className={th}>시각</th>
                <th className={th}>진단</th>
                <th className={th}>프로토콜</th>
                <th className={th}>메서드</th>
                <th className={th}>URL</th>
                <th className={th}>크기</th>
                <th className={th} />
              </tr>
            </thead>
            <tbody>
              {items.map((t) => (
                <tr key={t.id} className="border-t border-line">
                  <td className="whitespace-nowrap px-4 py-2 tabular text-xs text-faint">{formatTime(t.collected_at)}</td>
                  <td className="px-4 py-2">
                    <Link href={`/runs/${t.run_code}`} className="font-mono text-xs text-accent hover:underline">{t.run_code}</Link>
                  </td>
                  <td className="px-4 py-2 text-xs">{t.tool === "proxy.https" ? "HTTPS(복호화됨)" : "HTTP"}</td>
                  <td className="px-4 py-2 font-mono text-xs">{t.args.method}</td>
                  <td className="max-w-md truncate px-4 py-2 font-mono text-xs" title={t.args.url}>{t.args.url}</td>
                  <td className="px-4 py-2 tabular text-xs text-faint">{t.size != null ? formatBytes(t.size) : "-"}</td>
                  <td className="px-4 py-2 text-right">
                    {t.success && (
                      <a href={downloadUrl(t.run_id, t.id)} className="text-accent hover:underline" target="_blank" rel="noreferrer">
                        원본
                      </a>
                    )}
                  </td>
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
    </main>
  );
}
