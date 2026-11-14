"use client";

import { useState } from "react";
import { Check, RotateCw, X } from "lucide-react";

import { StatusBadge } from "@/components/StatusBadge";
import { btnPrimary, btnSecondary, Card, ConfirmDialog, Empty, SkeletonRows, useToast } from "@/components/ui";
import { api, ApiError, BacklogItem } from "@/lib/api";
import { formatTime } from "@/lib/labels";
import { needsApproval, th } from "../_shared";

// ── 작업 큐 ──
export function BacklogTab({ runId, items, onChanged }: { runId: string; items: BacklogItem[] | null; onChanged: () => void }) {
  const toast = useToast();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [onlyPending, setOnlyPending] = useState(false);
  const [retrying, setRetrying] = useState<BacklogItem | null>(null);
  const [retryingAll, setRetryingAll] = useState(false);

  async function retry(reason: string) {
    if (!retrying) return;
    setBusyId(retrying.id);
    try {
      await api.post(`/api/runs/${runId}/backlog/${retrying.id}/retry`, { reason });
      toast("ok", "재시도를 등록했습니다. 진단을 ‘재개’하면 다시 실행됩니다");
    } catch (e) {
      toast("danger", e instanceof ApiError ? e.message : "재시도 등록에 실패했습니다");
    } finally {
      setBusyId(null);
      setRetrying(null);
      onChanged();
    }
  }

  async function retryAll(reason: string) {
    setBusyId("__all__");
    try {
      const r = await api.post<{ count: number }>(`/api/runs/${runId}/backlog/retry-all`, { reason });
      toast("ok", `${r.count}건을 재시도 등록했습니다. 진단을 ‘재개’하면 다시 실행됩니다`);
    } catch (e) {
      toast("danger", e instanceof ApiError ? e.message : "일괄 재시도에 실패했습니다");
    } finally {
      setBusyId(null);
      setRetryingAll(false);
      onChanged();
    }
  }

  async function decide(itemId: string, reject: boolean) {
    setBusyId(itemId);
    try {
      await api.post(`/api/runs/${runId}/backlog/${itemId}/approve`, { reject });
      toast("ok", reject ? "작업을 거부했습니다" : "작업을 승인했습니다");
    } catch (e) {
      // 다른 라운드가 먼저 소비했거나 상태가 막 바뀐 경우가 흔하다 — 목록은 지우지 않고 알리기만 한다.
      toast("danger", e instanceof ApiError ? e.message : "승인 처리에 실패했습니다");
    } finally {
      setBusyId(null);
      onChanged(); // 성공/실패 모두 최신 상태로 다시 받는다
    }
  }

  if (!items) return <SkeletonRows />;
  if (items.length === 0) return <Empty title="작업이 없습니다" hint="진단이 시작되면 AI가 계획한 작업이 쌓입니다." />;

  const pending = items.filter(needsApproval).length;
  const retryable = items.filter((it) => !needsApproval(it) && (it.status === "FAILED" || it.status === "BLOCKED")).length;
  const rows = onlyPending ? items.filter(needsApproval) : items;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-2">
          <input type="checkbox" className="accent-[var(--accent)]" checked={onlyPending} onChange={(e) => setOnlyPending(e.target.checked)} />
          승인 대기만 보기 <span className="tabular text-warn">({pending})</span>
        </label>
        {retryable > 0 && (
          <button disabled={busyId !== null} onClick={() => setRetryingAll(true)} className={`${btnSecondary} ml-auto`}>
            <RotateCw className="h-4 w-4" aria-hidden /> 막힌 작업 전체 재시도 ({retryable})
          </button>
        )}
      </div>
      {rows.length === 0 ? (
        <Empty title="승인을 기다리는 작업이 없습니다" />
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-raised text-left text-muted">
              <tr>
                <th scope="col" className={th}>작업 종류</th>
                <th scope="col" className={th}>상태</th>
                <th scope="col" className={th}>사유</th>
                <th scope="col" className={`${th} text-right`}>시도</th>
                <th scope="col" className={th}>갱신 시각</th>
                <th scope="col" className={th}><span className="sr-only">승인</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((it) => (
                <tr key={it.id} className={`border-t border-line ${needsApproval(it) ? "bg-warn/5" : ""}`}>
                  <td className="px-4 py-2 font-mono text-xs">{it.kind}</td>
                  <td className="px-4 py-2"><StatusBadge value={it.status} /></td>
                  <td className="px-4 py-2 text-muted">{it.blocked_reason ?? it.reason}</td>
                  <td className="px-4 py-2 text-right">{it.attempts}</td>
                  <td className="px-4 py-2 text-muted">{formatTime(it.updated_at)}</td>
                  <td className="px-4 py-2 text-right">
                    {needsApproval(it) && (
                      <div className="flex justify-end gap-1.5">
                        <button disabled={busyId === it.id} onClick={() => decide(it.id, false)} className={btnPrimary}>
                          <Check className="h-4 w-4" aria-hidden /> 승인
                        </button>
                        <button disabled={busyId === it.id} onClick={() => decide(it.id, true)} className={btnSecondary}>
                          <X className="h-4 w-4" aria-hidden /> 거부
                        </button>
                      </div>
                    )}
                    {!needsApproval(it) && (it.status === "FAILED" || it.status === "BLOCKED") && (
                      <button disabled={busyId === it.id} onClick={() => setRetrying(it)} className={btnSecondary}>
                        <RotateCw className="h-4 w-4" aria-hidden /> 재시도
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      {retrying && (
        <ConfirmDialog title="이 작업을 다시 시도할까요?" confirmLabel="재시도 등록" withReason busy={busyId !== null} onConfirm={retry} onCancel={() => setRetrying(null)}>
          <span className="font-mono">{retrying.kind}</span> 작업이 처음 상태로 돌아가 정책 판정부터 다시 받습니다. 사유가 감사 로그에 기록됩니다.
        </ConfirmDialog>
      )}
      {retryingAll && (
        <ConfirmDialog title={`실패·차단된 작업 ${retryable}건을 모두 다시 시도할까요?`} confirmLabel="전체 재시도 등록" withReason busy={busyId !== null} onConfirm={retryAll} onCancel={() => setRetryingAll(false)}>
          승인 대기 중인 작업은 포함하지 않습니다(사람이 직접 승인/거부해야 함). 나머지는 각각 처음 상태로 돌아가 정책 판정부터 다시 받습니다. 사유가 감사 로그에 기록됩니다.
        </ConfirmDialog>
      )}
    </div>
  );
}
