"use client";

import Link from "next/link";
import { Copy, CopyPlus } from "lucide-react";

import { StatusBadge } from "@/components/StatusBadge";
import { btnSecondary, Card, useToast } from "@/components/ui";
import { BacklogItem, Finding, RunDetail } from "@/lib/api";
import { AUTONOMY_LABEL, describe, AGENT_ROLE_LABEL } from "@/lib/labels";
import { FindingFilter, NO_FILTER, REVIEW_PENDING, TabKey, needsApproval } from "../_shared";
import { RunControls } from "./RunControls";

// ── 요약 대시보드 ──
export function Summary({
  detail,
  findings,
  backlog,
  onFilterFindings,
  onShowTab,
  onChanged,
}: {
  detail: RunDetail;
  findings: Finding[] | null;
  backlog: BacklogItem[] | null;
  onFilterFindings: (f: FindingFilter) => void;
  onShowTab: (t: TabKey) => void;
  onChanged: () => void;
}) {
  const { run, usage, coverage, loop_running } = detail;
  const toast = useToast();

  const pendingReview = (findings ?? []).filter((f) => REVIEW_PENDING.includes(f.status)).length;
  const approvals = (backlog ?? []).filter(needsApproval).length;

  const q = coverage.backlog;
  const qTotal = Object.values(q).reduce((a, b) => a + b, 0);
  const segs: { key: string; cls: string }[] = [
    { key: "SUCCEEDED", cls: "bg-ok" },
    { key: "READY", cls: "bg-info" },
    { key: "PENDING", cls: "bg-faint" },
    { key: "BLOCKED", cls: "bg-warn" },
    { key: "FAILED", cls: "bg-danger" },
  ];

  async function copyId() {
    try {
      await navigator.clipboard.writeText(run.run_code);
      toast("ok", "진단 ID를 복사했습니다");
    } catch {
      toast("danger", "복사하지 못했습니다");
    }
  }

  return (
    <Card className="mb-4 p-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-mono text-lg font-semibold">{run.run_code}</h1>
            <button onClick={copyId} title="진단 ID 복사" aria-label="진단 ID 복사" className="rounded p-1 text-muted hover:bg-raised hover:text-ink">
              <Copy className="h-4 w-4" aria-hidden />
            </button>
            <Link href={`/runs/new?from=${encodeURIComponent(run.run_code)}`} className="flex items-center gap-1 rounded p-1 text-xs text-muted hover:bg-raised hover:text-ink" title="이 진단의 설정으로 새 진단 만들기">
              <CopyPlus className="h-4 w-4" aria-hidden /> 복제
            </Link>
            <StatusBadge value={run.status as string} />
            {loop_running && (
              <span className="h-2 w-2 animate-pulse rounded-full bg-info" role="img" aria-label="루프 동작 중" title="루프 동작 중" />
            )}
          </div>
          <p className="mt-1 text-sm text-muted">
            운영자 {run.operator as string} · 자율성 {AUTONOMY_LABEL[run.autonomy as string] ?? (run.autonomy as string)} · LLM{" "}
            {run.use_llm ? (run.llm_provider as string) : "사용 안 함"}
            {Object.keys((run.llm_agent_models ?? {}) as Record<string, string>).length > 0 && (
              <> · 에이전트별 모델{" "}
                {Object.entries(run.llm_agent_models as Record<string, string>)
                  .map(([role, m]) => `${AGENT_ROLE_LABEL[role] ?? role}=${m.replace(/^ollama:/, "")}`)
                  .join(", ")}
              </>
            )}
            {run.use_llm && (
              <> · 교차 검증{" "}
                {run.verifier_model ? (run.verifier_model as string).replace(/^ollama:/, "") : "없음"}
              </>
            )}
            {run.stop_reason ? <> · 종료 사유: {run.stop_reason as string}</> : null}
          </p>
        </div>
        <RunControls detail={detail} onChanged={onChanged} />
      </div>

      {detail.goals?.length > 0 && (
        <div className="mt-4 border-t border-line pt-4">
          <div className="mb-2 text-xs font-medium text-muted">
            목표{detail.goals.length > 1 ? ` · 대상별로 ${detail.goals.length}개로 나뉨` : ""}
          </div>
          <ul className="space-y-1 text-sm">
            {detail.goals.map((g, i) => (
              <li key={i} className="text-ink">
                {g.text}
                {g.targets.length > 0 && <span className="ml-2 text-xs text-muted">({g.targets.join(", ")})</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {approvals > 0 && (
        <div className="mt-4 flex items-center justify-between gap-3 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-sm">
          <span className="text-warn">사람의 승인을 기다리는 작업이 {approvals}건 있습니다. 승인하기 전에는 실행되지 않습니다.</span>
          <button onClick={() => onShowTab("backlog")} className={btnSecondary}>
            승인하러 가기
          </button>
        </div>
      )}

      <div className="mt-4 grid gap-4 border-t border-line pt-4 md:grid-cols-3">
        <div>
          <div className="mb-2 text-xs font-medium text-muted">발견 사항 · 검토 대기 {pendingReview}건</div>
          <button
            disabled={pendingReview === 0}
            onClick={() => {
              onFilterFindings({ ...NO_FILTER, statuses: REVIEW_PENDING });
              onShowTab("findings");
            }}
            className="text-xs text-accent hover:underline disabled:text-faint disabled:no-underline"
          >
            검토 대기 {pendingReview}건 보기
          </button>
        </div>

        <div>
          <div className="mb-2 text-xs font-medium text-muted">
            작업 큐 · 전체 <span className="tabular">{qTotal}</span>건
          </div>
          {qTotal === 0 ? (
            <p className="text-xs text-faint">아직 생성된 작업이 없습니다.</p>
          ) : (
            <>
              <div className="flex h-2 overflow-hidden rounded-full bg-raised" role="img" aria-label="작업 큐 상태 분포">
                {segs.map(({ key, cls }) =>
                  q[key] ? <div key={key} className={cls} style={{ width: `${(q[key] / qTotal) * 100}%` }} /> : null,
                )}
              </div>
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
                {Object.entries(q).map(([k, v]) => (
                  <span key={k} className="tabular">
                    {describe(k).label} {v}
                  </span>
                ))}
              </div>
            </>
          )}
        </div>

        <div>
          <div className="mb-2 text-xs font-medium text-muted">실행량</div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            <dt className="text-muted">도구 호출</dt>
            <dd className="tabular text-right">{usage.tools}</dd>
            <dt className="text-muted">탐색 도구 호출</dt>
            <dd className="tabular text-right">{usage.explore_tools}</dd>
            <dt className="text-muted">증적</dt>
            <dd className="tabular text-right">{usage.evidence}</dd>
            <dt className="text-muted">LLM 호출</dt>
            <dd className="tabular text-right">{usage.llm_calls}</dd>
          </dl>
        </div>
      </div>
    </Card>
  );
}
