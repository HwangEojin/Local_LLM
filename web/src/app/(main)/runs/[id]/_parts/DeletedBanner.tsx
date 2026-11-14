"use client";

import { useState } from "react";
import { RotateCcw } from "lucide-react";

import { btnSecondary, useToast } from "@/components/ui";
import { api, ApiError, RunDetail } from "@/lib/api";
import { formatTime } from "@/lib/labels";

export function DeletedBanner({ detail, onChanged }: { detail: RunDetail; onChanged: () => void }) {
  const { run } = detail;
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  if (!run.deleted_at) return null;

  async function restore() {
    setBusy(true);
    try {
      await api.post(`/api/runs/${run.run_code}/restore`, { reason: "복원" });
      toast("ok", `${run.run_code} 을(를) 복원했습니다`);
      onChanged();
    } catch (e) {
      toast("danger", e instanceof ApiError ? e.message : "복원에 실패했습니다");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div role="status" className="mb-4 flex items-center justify-between gap-3 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">
      <span>
        삭제된 진단입니다 ({formatTime(run.deleted_at)}
        {run.delete_reason ? ` · 사유: ${run.delete_reason}` : ""}). 목록에서 숨겨져 있고 복원하기 전에는 시작할 수 없습니다. 증적과 기록은 그대로입니다.
      </span>
      <button disabled={busy} onClick={restore} className={btnSecondary}>
        <RotateCcw className="h-4 w-4" aria-hidden /> 복원
      </button>
    </div>
  );
}
