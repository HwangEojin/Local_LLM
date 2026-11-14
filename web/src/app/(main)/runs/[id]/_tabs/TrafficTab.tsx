"use client";

import { Banner, Card, Empty, SkeletonRows } from "@/components/ui";
import { downloadUrl, TrafficEntry } from "@/lib/api";
import { formatBytes, formatTime } from "@/lib/labels";
import { useRunResource } from "@/lib/useRunResource";
import { th } from "../_shared";

// ── 트래픽(자체 레코딩 프록시) ──
export function TrafficTab({ runId }: { runId: string }) {
  const { data: items, error } = useRunResource<TrafficEntry[]>(`/api/runs/${runId}/traffic`, 6000);

  if (error && !items) return <Banner>{error}</Banner>;
  if (!items) return <SkeletonRows />;
  if (items.length === 0) {
    return <Empty title="기록된 트래픽이 없습니다" hint="workbench proxy 로 이 실행의 프록시를 띄우고 브라우저/도구를 그쪽으로 돌리면 여기에 쌓입니다." />;
  }

  return (
    <Card className="divide-y divide-line">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-faint">
          <tr>
            <th className={th}>시각</th>
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
              <td className="px-4 py-2 text-xs">{t.tool === "proxy.https" ? "HTTPS(복호화됨)" : "HTTP"}</td>
              <td className="px-4 py-2 font-mono text-xs">{t.args.method}</td>
              <td className="max-w-md truncate px-4 py-2 font-mono text-xs" title={t.args.url}>{t.args.url}</td>
              <td className="px-4 py-2 tabular text-xs text-faint">{t.size != null ? formatBytes(t.size) : "-"}</td>
              <td className="px-4 py-2 text-right">
                {t.success && (
                  <a href={downloadUrl(runId, t.id)} className="text-accent hover:underline" target="_blank" rel="noreferrer">
                    원본
                  </a>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
