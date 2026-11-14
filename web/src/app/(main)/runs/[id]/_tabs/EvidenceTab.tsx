"use client";

import { useState } from "react";
import { Download } from "lucide-react";

import { StatusBadge } from "@/components/StatusBadge";
import { Banner, Card, Empty, inputCls, SkeletonRows } from "@/components/ui";
import { downloadUrl, Evidence } from "@/lib/api";
import { formatBytes, formatTime } from "@/lib/labels";
import { useRunResource } from "@/lib/useRunResource";
import { th } from "../_shared";

// ── 증적 ──
export function EvidenceTab({ runId }: { runId: string }) {
  const { data: items, error } = useRunResource<Evidence[]>(`/api/runs/${runId}/evidence`, 0);
  const [q, setQ] = useState("");

  if (error && !items) return <Banner>{error}</Banner>;
  if (!items) return <SkeletonRows />;
  if (items.length === 0) return <Empty title="수집된 증적이 없습니다" />;

  const needle = q.trim().toLowerCase();
  const rows = items.filter((e) => !needle || e.tool.toLowerCase().includes(needle) || (e.requirement_id ?? "").toLowerCase().includes(needle));

  return (
    <div>
      <div className="mb-3 flex items-center gap-3">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="도구·요구사항 검색" aria-label="증적 검색" className={`${inputCls} !w-60`} />
        <span className="text-xs text-faint tabular">{rows.length} / {items.length}건</span>
      </div>
      <Card className="overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-raised text-left text-muted">
            <tr>
              <th scope="col" className={th}>도구</th>
              <th scope="col" className={th}>요구사항</th>
              <th scope="col" className={`${th} text-right`}>크기</th>
              <th scope="col" className={th}>무결성</th>
              <th scope="col" className={th}>수집 시각</th>
              <th scope="col" className={th}><span className="sr-only">다운로드</span></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.id} className="border-t border-line" title={e.integrity_detail ?? undefined}>
                <td className="px-4 py-2 font-mono text-xs">
                  {e.tool}
                  {e.truncated && <span className="ml-1 text-warn">(일부 잘림)</span>}
                </td>
                <td className="px-4 py-2 text-muted">{e.requirement_id}</td>
                <td className="px-4 py-2 text-right text-muted">{e.success ? formatBytes(e.size) : "-"}</td>
                <td className="px-4 py-2">
                  {e.success ? (
                    e.integrity === "확인됨" ? <StatusBadge value="SUCCEEDED" /> : <span className="text-xs font-medium text-danger">손상됨</span>
                  ) : (
                    <span className="text-xs text-danger">{e.error ?? "수집 실패"}</span>
                  )}
                </td>
                <td className="px-4 py-2 text-muted">{formatTime(e.collected_at)}</td>
                <td className="px-4 py-2 text-right">
                  {e.success && e.integrity === "확인됨" && (
                    <a href={downloadUrl(runId, e.id)} className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
                      <Download className="h-3.5 w-3.5" aria-hidden /> 다운로드
                    </a>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
