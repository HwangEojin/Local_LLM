"use client";

import { useState } from "react";
import { Check, Download, FileText } from "lucide-react";

import { Banner, btnGhost, btnPrimary, btnSecondary, Card, Empty, inputCls, SkeletonRows, useToast } from "@/components/ui";
import { api, ApiError, ReportFile, reportDownloadUrl } from "@/lib/api";
import { formatBytes, formatTime } from "@/lib/labels";
import { useRunResource } from "@/lib/useRunResource";

// ── 결과보고서(Word/Excel, CLAUDE.md §12) ──
export function ReportTab({ runId, completed }: { runId: string; completed: boolean }) {
  const toast = useToast();
  const { data: files, reload } = useRunResource<ReportFile[]>(`/api/runs/${runId}/report`, 0);
  const [meta, setMeta] = useState('{\n  "customer": "",\n  "project": "",\n  "targets": [{"target_id": "", "name": ""}]\n}');
  const [kind, setKind] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function generate() {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(meta);
    } catch {
      setError("보고서 정보는 올바른 JSON 이어야 합니다");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/runs/${runId}/report`, { meta: parsed, kind: kind.trim() || null, confirm: true });
      toast("ok", "보고서를 생성했습니다");
      reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "생성에 실패했습니다");
    } finally {
      setBusy(false);
    }
  }

  async function approve(f: ReportFile) {
    setBusy(true);
    try {
      await api.post(`/api/runs/${runId}/report/${encodeURIComponent(f.file)}/approve`, { confirm: true });
      toast("ok", "승인했습니다");
      reload();
    } catch (e) {
      toast("danger", e instanceof ApiError ? e.message : "승인에 실패했습니다");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      {!completed && <Banner tone="warn">완료(COMPLETED)된 진단만 결과보고서를 생성할 수 있습니다.</Banner>}
      <Card className="space-y-3 p-4">
        <h3 className="text-sm font-semibold">결과보고서 생성</h3>
        {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
        <div>
          <label className="mb-1 block text-xs text-muted">
            보고서 정보(JSON, customer/project/targets 필수) — 체크리스트 기반 실행의 기본(내부 점검) 요약은 생략할 수 있습니다
          </label>
          <textarea className={`${inputCls} h-32 font-mono text-xs`} value={meta} onChange={(e) => setMeta(e.target.value)} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted">템플릿 유형(회사 템플릿을 쓸 때만, 예: web)</label>
          <input className={`${inputCls} !w-40`} value={kind} onChange={(e) => setKind(e.target.value)} placeholder="생략 가능" />
        </div>
        <button disabled={busy || !completed} onClick={generate} className={btnPrimary}>
          <FileText className="h-4 w-4" aria-hidden /> 생성
        </button>
        <p className="text-xs text-faint">
          생성된 문서는 사람이 검토해 아래에서 승인하기 전까지 초안입니다(CLAUDE.md §10). 승인 기록은 감사 로그에 남습니다.
        </p>
      </Card>

      {!files ? (
        <SkeletonRows />
      ) : files.length === 0 ? (
        <Empty title="생성된 보고서가 없습니다" />
      ) : (
        <Card className="divide-y divide-line">
          {files.map((f) => (
            <div key={f.file} className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm">
              <div className="min-w-0">
                <div className="font-mono text-xs">{f.file}</div>
                <div className="text-xs text-faint">
                  {formatBytes(f.size)} · {formatTime(f.modified_at)}
                  {f.approved_by && <> · 승인: {f.approved_by} ({formatTime(f.approved_at as string)})</>}
                </div>
                {f.stale && (
                  <div className="mt-1 text-xs font-medium text-warn">
                    ⚠ 승인 이후 finding {f.edited_after_approval}건 편집됨 — 재생성 필요
                  </div>
                )}
              </div>
              <div className="flex shrink-0 gap-2">
                <a href={reportDownloadUrl(runId, f.file)} className={btnGhost} download>
                  <Download className="h-4 w-4" aria-hidden /> 다운로드
                </a>
                {!f.approved_by && (
                  <button disabled={busy} onClick={() => approve(f)} className={btnSecondary}>
                    <Check className="h-4 w-4" aria-hidden /> 승인
                  </button>
                )}
              </div>
            </div>
          ))}
        </Card>
      )}
    </div>
  );
}
