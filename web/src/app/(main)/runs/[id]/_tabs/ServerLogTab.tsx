"use client";

import { useEffect, useRef, useState } from "react";
import { Copy, Pause, Play } from "lucide-react";

import { Banner, btnGhost, btnSecondary, Empty, SkeletonRows, useToast } from "@/components/ui";
import { ServerLogEntry } from "@/lib/api";
import { formatTime } from "@/lib/labels";
import { useRunResource } from "@/lib/useRunResource";

// ── 서버 로그 ──
export function ServerLogTab({ runId }: { runId: string }) {
  const toast = useToast();
  const [paused, setPaused] = useState(false);
  const { data: items, error } = useRunResource<ServerLogEntry[]>(`/api/runs/${runId}/server-log`, 2000, !paused);
  const [follow, setFollow] = useState(true);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (follow && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [items, follow]);

  async function copy() {
    try {
      await navigator.clipboard.writeText((items ?? []).map((l) => `${l.ts} ${l.message}`).join("\n"));
      toast("ok", "로그를 복사했습니다");
    } catch {
      toast("danger", "복사하지 못했습니다");
    }
  }

  if (error && !items) return <Banner>{error}</Banner>;
  if (!items) return <SkeletonRows />;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
        <button onClick={() => setPaused((p) => !p)} className={btnSecondary} aria-pressed={paused}>
          {paused ? <Play className="h-4 w-4" aria-hidden /> : <Pause className="h-4 w-4" aria-hidden />}
          {paused ? "갱신 재개" : "갱신 일시정지"}
        </button>
        <label className="flex items-center gap-2">
          <input type="checkbox" className="accent-[var(--accent)]" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
          최신 줄 따라가기
        </label>
        <button onClick={copy} disabled={items.length === 0} className={btnGhost}>
          <Copy className="h-4 w-4" aria-hidden /> 복사
        </button>
        <span className="ml-auto text-xs text-faint tabular">{items.length}줄{paused ? " · 일시정지됨" : ""}</span>
      </div>
      {items.length === 0 ? (
        <Empty title="아직 서버 로그가 없습니다" hint="진단을 시작하면 진행 메시지가 여기에 쌓입니다." />
      ) : (
        <div ref={boxRef} className="max-h-[32rem] overflow-auto rounded-lg border border-line bg-canvas p-3 font-mono text-xs leading-relaxed">
          {items.map((l, i) => (
            <div key={i} className="whitespace-pre-wrap">
              <span className="text-faint">{formatTime(l.ts)}</span> {l.message}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
