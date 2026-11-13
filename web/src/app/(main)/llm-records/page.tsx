"use client";

// LLM 호출 기록 전역 화면(레퍼런스 프로젝트 llmrec 참고, UPGRADE_PLAN.md 재점검에서 찾은 갭 — 2026-11). 새 진단 로직
// 없음: /api/llm-records(model_calls 를 runs 와 조인해 조회만 함)만 읽는다. 원문은 저장 시점에 이미 마스킹돼
// 있다(explore.py 의 _ModelLog.record_model_call).
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";

import { Banner, btnGhost, Card, Empty, inputCls, SkeletonRows } from "@/components/ui";
import { api, LlmRecord } from "@/lib/api";
import { formatTime } from "@/lib/labels";
import { usePolling } from "@/lib/usePolling";

const PAGE = 50;

export default function LlmRecordsPage() {
  const [items, setItems] = useState<LlmRecord[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState("");
  const [offset, setOffset] = useState(0);

  const load = useCallback(() => {
    const p = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
    if (run.trim()) p.set("run", run.trim());
    api
      .get<{ items: LlmRecord[]; total: number }>(`/api/llm-records?${p.toString()}`)
      .then((r) => { setItems(r.items); setTotal(r.total); setError(null); })
      .catch((e) => setError(e.message));
  }, [run, offset]);
  useEffect(load, [load]);
  usePolling(load, 15000);

  const page = Math.floor(offset / PAGE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  return (
    <main className="mx-auto w-full max-w-6xl space-y-4 p-6">
      <h1 className="text-lg font-semibold">LLM 호출 기록</h1>
      <p className="text-sm text-muted">모든 진단의 LLM 요청/응답 원문을 가로질러 봅니다(비밀 값은 저장 전에 가려집니다).</p>
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
        <Empty title="LLM 호출 기록이 없습니다" />
      ) : (
        <div className="space-y-2">
          {items.map((r) => (
            <Card key={r.id} className="space-y-2 p-3">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="font-mono font-medium">{r.model}</span>
                <span className="rounded-full bg-raised px-2 py-0.5 text-muted">{r.purpose}</span>
                <span className={`rounded-full px-2 py-0.5 ${r.ok ? "bg-ok/15 text-ok" : "bg-danger/15 text-danger"}`}>{r.ok ? "성공" : "실패"}</span>
                <Link href={`/runs/${r.run_code}`} className="font-mono text-accent hover:underline">{r.run_code}</Link>
                {(r.prompt_tokens != null || r.completion_tokens != null) && (
                  <span className="text-faint">토큰 {(r.prompt_tokens ?? 0) + (r.completion_tokens ?? 0)}</span>
                )}
                {r.duration_ms != null && <span className="text-faint">{r.duration_ms}ms</span>}
                <span className="ml-auto text-faint">{formatTime(r.ts)}</span>
              </div>
              {r.error && <p className="text-xs text-danger">{r.error}</p>}
              {r.request_text && (
                <details>
                  <summary className="cursor-pointer text-xs text-muted">요청 원문 보기</summary>
                  <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-canvas p-2 text-xs">{r.request_text}</pre>
                </details>
              )}
              {r.response_text && (
                <details>
                  <summary className="cursor-pointer text-xs text-muted">응답 원문 보기</summary>
                  <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-canvas p-2 text-xs">{r.response_text}</pre>
                </details>
              )}
            </Card>
          ))}
        </div>
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
