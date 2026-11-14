"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Play, Square, Trash2, Undo2 } from "lucide-react";

import { btnDanger, btnGhost, btnPrimary, btnSecondary, ConfirmDialog, useToast } from "@/components/ui";
import { api, ApiError, RunDetail } from "@/lib/api";

// ── 실행 제어 ──
export function RunControls({ detail, onChanged }: { detail: RunDetail; onChanged: () => void }) {
  const { run, loop_running } = detail;
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const router = useRouter();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const deleted = !!run.deleted_at; // 삭제된 진단은 복원하기 전에는 시작·취소할 수 없다
  const canStart = run.status !== "CANCELLED" && !loop_running && !deleted;
  const canCancel = run.status !== "CANCELLED" && run.status !== "COMPLETED" && !deleted;

  async function remove(reason: string) {
    setBusy(true);
    try {
      await api.post(`/api/runs/${run.run_code}/delete`, { reason, confirm: true });
      toast("ok", `${run.run_code} 을(를) 삭제했습니다`);
      router.push("/runs");
    } catch (e) {
      toast("danger", e instanceof ApiError ? e.message : "삭제에 실패했습니다");
      setBusy(false);
      setConfirmDelete(false);
    }
  }

  async function act(path: string, ok: string, fail: string) {
    setBusy(true);
    try {
      await api.post(`/api/runs/${run.id}/${path}`);
      toast("ok", ok);
      onChanged();
    } catch (e) {
      toast("danger", e instanceof ApiError ? e.message : fail);
    } finally {
      setBusy(false);
      setConfirmCancel(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <button
        disabled={busy || !canStart}
        onClick={() => act("start", "진단을 시작했습니다", "시작에 실패했습니다")}
        className={btnPrimary}
      >
        <Play className="h-4 w-4" aria-hidden />
        {run.status === "CREATED" ? "시작" : "재개"}
      </button>
      {confirmCancel ? (
        <>
          <button disabled={busy} onClick={() => act("cancel", "진단을 취소했습니다", "취소에 실패했습니다")} className={btnDanger}>
            <Square className="h-4 w-4" aria-hidden /> 취소 확정
          </button>
          <button onClick={() => setConfirmCancel(false)} className={btnGhost}>
            <Undo2 className="h-4 w-4" aria-hidden /> 돌아가기
          </button>
        </>
      ) : (
        <button disabled={busy || !canCancel} onClick={() => setConfirmCancel(true)} className={btnSecondary}>
          <Square className="h-4 w-4" aria-hidden /> 취소
        </button>
      )}
      <button
        disabled={busy || loop_running || deleted}
        onClick={() => setConfirmDelete(true)}
        className={btnGhost}
        title={loop_running ? "실행 중인 진단은 먼저 취소해야 삭제할 수 있습니다" : "진단 삭제"}
        aria-label="진단 삭제"
      >
        <Trash2 className="h-4 w-4" aria-hidden />
      </button>
      {confirmDelete && (
        <ConfirmDialog title={`${run.run_code} 을(를) 삭제할까요?`} confirmLabel="삭제" danger withReason busy={busy} onConfirm={remove} onCancel={() => setConfirmDelete(false)}>
          목록에서 숨겨지고 시작할 수 없게 됩니다. 증적, 발견 사항, 감사 기록은 그대로 보존되며 ‘삭제된 진단 보기’에서 복원할 수 있습니다.
        </ConfirmDialog>
      )}
    </div>
  );
}
