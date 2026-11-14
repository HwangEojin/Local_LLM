"use client";

import { useState } from "react";

import { Banner, btnPrimary, Card, Empty, inputCls, SkeletonRows, useToast } from "@/components/ui";
import { api, ApiError, Question } from "@/lib/api";
import { formatTime } from "@/lib/labels";
import { useRunResource } from "@/lib/useRunResource";

// ── 질문(LLM 의 사이드 질문, human-in-the-loop) ──
export function ChatTab({ runId }: { runId: string }) {
  const toast = useToast();
  const { data: items, error, reload } = useRunResource<Question[]>(`/api/runs/${runId}/questions`, 6000);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);

  async function answer(q: Question) {
    const text = (drafts[q.id] ?? "").trim();
    if (!text) return;
    setBusyId(q.id);
    try {
      await api.post(`/api/runs/${runId}/questions/${q.id}/answer`, { answer: text });
      toast("ok", "답변을 기록했습니다. 다음 계획 주기에서 LLM 이 이어서 탐색합니다.");
      reload();
    } catch (e) {
      toast("danger", e instanceof ApiError ? e.message : "답변 기록에 실패했습니다");
    } finally {
      setBusyId(null);
    }
  }

  if (error && !items) return <Banner>{error}</Banner>;
  if (!items) return <SkeletonRows />;
  if (items.length === 0) {
    return <Empty title="LLM 이 되물은 질문이 없습니다" hint="탐색 중 막히면 여기에 질문이 나타납니다 — 한 번에 미답변 질문은 하나뿐입니다." />;
  }

  return (
    <div className="space-y-3">
      {items.map((q) => (
        <Card key={q.id} className="space-y-2 p-4">
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm text-ink">{q.payload.text}</p>
            <span className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${
              q.state === "OPEN" ? "border-warn text-warn" : "border-line text-faint"
            }`}>
              {q.state === "OPEN" ? "답변 대기" : "답변됨"}
            </span>
          </div>
          {q.payload.answer ? (
            <p className="rounded-md bg-raised p-2 text-sm text-muted">
              <span className="font-medium text-ink">{q.payload.answered_by}:</span> {q.payload.answer}
            </p>
          ) : (
            <div className="flex gap-2">
              <input
                className={inputCls + " flex-1"}
                placeholder="답변을 입력하세요"
                value={drafts[q.id] ?? ""}
                onChange={(e) => setDrafts((d) => ({ ...d, [q.id]: e.target.value }))}
                onKeyDown={(e) => { if (e.key === "Enter") answer(q); }}
              />
              <button onClick={() => answer(q)} disabled={busyId === q.id} className={btnPrimary}>
                답변 기록
              </button>
            </div>
          )}
          <span className="text-xs text-faint">{formatTime(q.created_at)}</span>
        </Card>
      ))}
    </div>
  );
}
