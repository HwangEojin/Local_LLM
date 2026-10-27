"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Check, Copy, CopyPlus, Download, Pause, Play, RotateCcw, RotateCw, Square, Trash2, Undo2, X } from "lucide-react";

import { AuthGate } from "@/components/AuthGate";
import { FindingPanel, EvidenceViewer } from "@/components/FindingPanel";
import { Header } from "@/components/Header";
import { StatusBadge } from "@/components/StatusBadge";
import {
  Banner,
  btnDanger,
  btnGhost,
  btnPrimary,
  btnSecondary,
  Card,
  ConfirmDialog,
  Empty,
  inputCls,
  SkeletonRows,
  useToast,
} from "@/components/ui";
import {
  ActivityEntry,
  api,
  ApiError,
  BacklogItem,
  downloadUrl,
  Evidence,
  Finding,
  FINDING_STATUSES,
  findingsExportUrl,
  Question,
  RunDetail,
  ServerLogEntry,
  TrafficEntry,
} from "@/lib/api";
import {
  AUTONOMY_LABEL,
  describe,
  formatBytes,
  formatTime,
  SEVERITY_ORDER,
  VERIFICATION_LABEL,
  AGENT_ROLE_LABEL,
} from "@/lib/labels";
import { usePolling } from "@/lib/usePolling";
import { useRunResource } from "@/lib/useRunResource";

type TabKey = "findings" | "backlog" | "assets" | "graph" | "traffic" | "chat" | "evidence" | "activity" | "server-log";
const TAB_KEYS: TabKey[] = ["findings", "backlog", "assets", "graph", "traffic", "chat", "evidence", "activity", "server-log"];
const TAB_LABEL: Record<TabKey, string> = {
  findings: "발견 사항",
  backlog: "작업 큐",
  assets: "자산",
  graph: "탐색 그래프",
  traffic: "트래픽",
  chat: "질문",
  evidence: "증적",
  activity: "실행 기록",
  "server-log": "서버 로그",
};

type FindingFilter = { statuses: string[]; severities: string[]; q: string };
const NO_FILTER: FindingFilter = { statuses: [], severities: [], q: "" };
const REVIEW_PENDING = ["CANDIDATE", "NEEDS_REVIEW"];

const needsApproval = (it: BacklogItem) => it.status === "BLOCKED" && it.policy_class === "ALLOW_WITH_APPROVAL";

const chip = (on: boolean) =>
  `rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
    on ? "border-accent bg-accent/15 text-accent" : "border-line text-muted hover:border-faint hover:text-ink"
  }`;

const th = "px-4 py-2 font-medium";

// ── 실행 제어 ──
function RunControls({ detail, onChanged }: { detail: RunDetail; onChanged: () => void }) {
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

function DeletedBanner({ detail, onChanged }: { detail: RunDetail; onChanged: () => void }) {
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

// ── 요약 대시보드 ──
function Summary({
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

  const bySeverity = useMemo(() => {
    const m: Record<string, number> = {};
    (findings ?? []).forEach((f) => (m[f.severity] = (m[f.severity] ?? 0) + 1));
    return m;
  }, [findings]);
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
          <div className="mb-2 text-xs font-medium text-muted">발견 사항 · 심각도별</div>
          <div className="flex flex-wrap gap-1.5">
            {SEVERITY_ORDER.map((s) => (
              <button
                key={s}
                disabled={!bySeverity[s]}
                onClick={() => {
                  onFilterFindings({ ...NO_FILTER, severities: [s] });
                  onShowTab("findings");
                }}
                className="inline-flex items-center gap-1.5 rounded-md border border-line px-2 py-1 text-xs transition-colors hover:bg-raised disabled:opacity-40 disabled:hover:bg-transparent"
              >
                <StatusBadge value={s} />
                <span className="tabular font-medium">{bySeverity[s] ?? 0}</span>
              </button>
            ))}
          </div>
          <button
            disabled={pendingReview === 0}
            onClick={() => {
              onFilterFindings({ ...NO_FILTER, statuses: REVIEW_PENDING });
              onShowTab("findings");
            }}
            className="mt-2 text-xs text-accent hover:underline disabled:text-faint disabled:no-underline"
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

// ── 발견 사항 ──
function FindingsTab({
  runId,
  findings,
  filter,
  setFilter,
  selectedId,
  onSelect,
}: {
  runId: string;
  findings: Finding[] | null;
  filter: FindingFilter;
  setFilter: (f: FindingFilter) => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const toggle = (key: "statuses" | "severities", v: string) =>
    setFilter({ ...filter, [key]: filter[key].includes(v) ? filter[key].filter((x) => x !== v) : [...filter[key], v] });

  const rows = useMemo(() => {
    if (!findings) return null;
    const q = filter.q.trim().toLowerCase();
    return findings
      .filter((f) => !filter.statuses.length || filter.statuses.includes(f.status))
      .filter((f) => !filter.severities.length || filter.severities.includes(f.severity))
      .filter((f) => !q || f.title.toLowerCase().includes(q) || f.rationale.toLowerCase().includes(q))
      .sort((a, b) => {
        const sa = SEVERITY_ORDER.indexOf(a.severity as (typeof SEVERITY_ORDER)[number]);
        const sb = SEVERITY_ORDER.indexOf(b.severity as (typeof SEVERITY_ORDER)[number]);
        return (sa < 0 ? 99 : sa) - (sb < 0 ? 99 : sb) || b.created_at.localeCompare(a.created_at);
      });
  }, [findings, filter]);

  if (!findings) return <SkeletonRows />;
  if (findings.length === 0) return <Empty title="아직 발견 사항이 없습니다" hint="진단이 진행되면 취약점 후보가 여기에 쌓입니다." />;

  const filtering = filter.statuses.length > 0 || filter.severities.length > 0 || filter.q !== "";

  return (
    <div>
      <div className="mb-3 space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <input
            value={filter.q}
            onChange={(e) => setFilter({ ...filter, q: e.target.value })}
            placeholder="제목·근거 검색"
            aria-label="발견 사항 검색"
            className={`${inputCls} !w-60`}
          />
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="판정 상태 필터">
            {FINDING_STATUSES.map((s) => (
              <button key={s} aria-pressed={filter.statuses.includes(s)} onClick={() => toggle("statuses", s)} className={chip(filter.statuses.includes(s))}>
                {describe(s).label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="심각도 필터">
          {SEVERITY_ORDER.map((s) => (
            <button key={s} aria-pressed={filter.severities.includes(s)} onClick={() => toggle("severities", s)} className={chip(filter.severities.includes(s))}>
              {describe(s).label}
            </button>
          ))}
          {filtering && (
            <button onClick={() => setFilter(NO_FILTER)} className={btnGhost}>
              필터 초기화
            </button>
          )}
          <a href={findingsExportUrl(runId, "csv")} className={btnGhost} download>
            <Download className="h-4 w-4" aria-hidden /> CSV
          </a>
          <a href={findingsExportUrl(runId, "json")} className={btnGhost} download>
            <Download className="h-4 w-4" aria-hidden /> JSON
          </a>
          <span className="ml-auto text-xs text-faint tabular">
            {rows?.length ?? 0} / {findings.length}건
          </span>
        </div>
      </div>

      {rows && rows.length === 0 ? (
        <Empty title="조건에 맞는 발견 사항이 없습니다" hint="필터를 줄여 보세요." />
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-raised text-left text-muted">
              <tr>
                <th scope="col" className={th}>제목</th>
                <th scope="col" className={th}>심각도</th>
                <th scope="col" className={th}>판정</th>
                <th scope="col" className={th}>검증 방식</th>
                <th scope="col" className={th}>검토자</th>
                <th scope="col" className={th}>발견 시각</th>
              </tr>
            </thead>
            <tbody>
              {rows?.map((f) => (
                <tr
                  key={f.id}
                  tabIndex={0}
                  aria-selected={selectedId === f.id}
                  onClick={() => onSelect(f.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(f.id);
                    }
                  }}
                  className={`cursor-pointer border-t border-line transition-colors hover:bg-raised/60 ${selectedId === f.id ? "bg-raised" : ""}`}
                >
                  <td className="px-4 py-2 font-medium">{f.title}</td>
                  <td className="px-4 py-2"><StatusBadge value={f.severity} /></td>
                  <td className="px-4 py-2"><StatusBadge value={f.status} /></td>
                  <td className="px-4 py-2 text-muted">{VERIFICATION_LABEL[f.verification] ?? f.verification}</td>
                  <td className="px-4 py-2 text-muted">{f.reviewed_by ?? "검토 전"}</td>
                  <td className="px-4 py-2 text-muted">{formatTime(f.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

// ── 작업 큐 ──
function BacklogTab({ runId, items, onChanged }: { runId: string; items: BacklogItem[] | null; onChanged: () => void }) {
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

// ── 증적 ──
function EvidenceTab({ runId }: { runId: string }) {
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

// ── 자산 ──
type AssetRow = { id: string; target_id: string; kind: string; value: string; company: string | null; verification: string; source: string };
type AssetEdgeRow = { src: string; rel: string; dst: string; source: string };

type AssetsData = { assets: AssetRow[]; edges: AssetEdgeRow[] };
const useAssets = (runId: string) => useRunResource<AssetsData>(`/api/runs/${runId}/assets`, 6000);

function AssetsTab({ runId }: { runId: string }) {
  const { data, error } = useAssets(runId);

  if (error && !data) return <Banner>{error}</Banner>;
  if (!data) return <SkeletonRows />;
  if (data.assets.length === 0) return <Empty title="등록된 자산이 없습니다" />;

  return (
    <div className="space-y-4">
      <Card className="divide-y divide-line">
        {data.assets.map((a) => (
          <div key={a.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 p-3 text-xs">
            <span className="tabular text-faint">{a.target_id}</span>
            <span className="font-mono text-sm font-semibold text-ink">{a.kind}:{a.value}</span>
            {a.company && <span className="rounded bg-[var(--accent)]/10 px-1.5 py-0.5 text-[var(--accent)]">{a.company}</span>}
            <span className="text-faint">{a.verification}</span>
            <span className="text-faint">출처: {a.source}</span>
          </div>
        ))}
      </Card>
      {data.edges.length > 0 && (
        <Card className="divide-y divide-line">
          {data.edges.map((e, i) => (
            <div key={i} className="p-2 font-mono text-xs text-muted">
              {e.src} <span className="text-faint">-{e.rel}-&gt;</span> {e.dst}
            </div>
          ))}
        </Card>
      )}
    </div>
  );
}

// ── 탐색 그래프 ──
function GraphTab({ runId }: { runId: string }) {
  const { data, error } = useAssets(runId);

  // data 참조가 폴링마다 바뀌지 않는 한(usePolling 은 실제로 바뀐 응답만 setData 한다) 재계산하지 않는다.
  const graph = useMemo(() => {
    const assets = data?.assets ?? [], edges = data?.edges ?? [];
    const labels = Array.from(new Set([
      ...assets.map((a) => `${a.kind}:${a.value}`),
      ...edges.flatMap((e) => [e.src, e.dst]),
    ]));
    const kindByLabel = new Map(assets.map((a) => [`${a.kind}:${a.value}`, a.kind]));
    const size = Math.max(420, Math.min(900, 140 + labels.length * 36));
    const cx = size / 2, cy = size / 2, radius = size / 2 - 70;
    const pos = new Map(
      labels.map((label, i) => {
        const angle = (i / labels.length) * 2 * Math.PI;
        return [label, { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) }] as const;
      }),
    );
    return { labels, kindByLabel, pos, size };
  }, [data]);

  if (error && !data) return <Banner>{error}</Banner>;
  if (!data) return <SkeletonRows />;
  if (graph.labels.length === 0) return <Empty title="그릴 자산 관계가 없습니다" />;

  const { labels, kindByLabel, pos, size } = graph;
  const kindOf = (label: string) => kindByLabel.get(label) ?? label.split(":")[0] ?? "asset";
  const KIND_COLOR: Record<string, string> = {
    host: "var(--accent)", url: "#7c3aed", service: "#0ea5e9", path: "#f59e0b",
  };

  return (
    <Card className="overflow-auto p-4">
      <svg width={size} height={size} className="mx-auto" role="img" aria-label="자산·탐색 관계 그래프">
        {data.edges.map((e, i) => {
          const s = pos.get(e.src), d = pos.get(e.dst);
          if (!s || !d) return null;
          return (
            <g key={i}>
              <line x1={s.x} y1={s.y} x2={d.x} y2={d.y} stroke="var(--line)" strokeWidth={1.5} />
              <text x={(s.x + d.x) / 2} y={(s.y + d.y) / 2} fontSize={10} fill="var(--muted)" textAnchor="middle">
                {e.rel}
              </text>
            </g>
          );
        })}
        {labels.map((label) => {
          const p = pos.get(label);
          if (!p) return null;
          const kind = kindOf(label);
          return (
            <g key={label}>
              <circle cx={p.x} cy={p.y} r={7} fill={KIND_COLOR[kind] ?? "var(--muted)"} />
              <text x={p.x} y={p.y - 11} fontSize={11} fill="var(--ink)" textAnchor="middle" className="font-mono">
                {label.length > 28 ? `${label.slice(0, 26)}…` : label}
              </text>
            </g>
          );
        })}
      </svg>
    </Card>
  );
}

// ── 트래픽(자체 레코딩 프록시) ──
function TrafficTab({ runId }: { runId: string }) {
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

// ── 질문(LLM 의 사이드 질문, human-in-the-loop) ──
function ChatTab({ runId }: { runId: string }) {
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

// ── 실행 기록 ──
function ActivityTab({ runId }: { runId: string }) {
  const { data: items, error } = useRunResource<ActivityEntry[]>(`/api/runs/${runId}/activity`, 4000);
  const [q, setQ] = useState("");
  const [onlyProblems, setOnlyProblems] = useState(false);

  if (error && !items) return <Banner>{error}</Banner>;
  if (!items) return <SkeletonRows />;
  if (items.length === 0) return <Empty title="아직 실행된 도구 호출이 없습니다" />;

  const needle = q.trim().toLowerCase();
  const rows = items
    .filter((t) => !onlyProblems || t.status === "FAILED" || t.status === "BLOCKED" || t.error)
    .filter((t) => !needle || t.tool.toLowerCase().includes(needle) || JSON.stringify(t.args ?? "").toLowerCase().includes(needle) || (t.asset_value ?? "").toLowerCase().includes(needle));

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="도구·명령·대상 검색" aria-label="실행 기록 검색" className={`${inputCls} !w-60`} />
        <label className="flex items-center gap-2">
          <input type="checkbox" className="accent-[var(--accent)]" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} />
          실패·차단만
        </label>
        <span className="ml-auto text-xs text-faint tabular">{rows.length} / {items.length}건</span>
      </div>
      <Card className="divide-y divide-line">
        {rows.length === 0 && <p className="p-4 text-sm text-faint">조건에 맞는 기록이 없습니다.</p>}
        {rows.map((t) => (
          <div key={t.id} className="p-3 text-xs">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="tabular text-faint">{formatTime(t.started_at)}</span>
              <span className="tabular text-muted">#{t.step_no}</span>
              <span className="font-mono text-sm font-semibold text-ink">{t.tool}</span>
              <StatusBadge value={t.status} />
              <span className="text-faint">정책: {t.policy_decision}</span>
              {t.asset_kind && <span className="font-mono text-muted">{t.asset_kind}:{t.asset_value}</span>}
            </div>
            {t.args != null && <div className="mt-1 break-all font-mono text-muted">명령 인자: {JSON.stringify(t.args)}</div>}
            {t.policy_reason && <div className="mt-0.5 text-faint">정책 사유: {t.policy_reason}</div>}
            {t.error && <div className="mt-0.5 text-danger">오류: {t.error}</div>}
            {t.evidence_id && (
              <div className="mt-1">
                <EvidenceViewer runId={runId} evidenceId={t.evidence_id} mediaType={t.evidence_media_type} size={t.evidence_size} />
              </div>
            )}
          </div>
        ))}
      </Card>
    </div>
  );
}

// ── 서버 로그 ──
function ServerLogTab({ runId }: { runId: string }) {
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

// ── 본문 ──
function RunDetailBody({ runId }: { runId: string }) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [backlog, setBacklog] = useState<BacklogItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 이 컴포넌트는 AuthGate 통과 후(클라이언트에서만) 렌더되므로 lazy 초기화에서 window 를 읽어도 hydration 불일치가 없다.
  const [tab, setTabState] = useState<TabKey>(() => {
    const h = window.location.hash.slice(1) as TabKey;
    return TAB_KEYS.includes(h) ? h : "findings";
  });
  const [filter, setFilter] = useState<FindingFilter>(NO_FILTER);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // 탭을 URL 해시에 보관해 새로고침·링크 공유 후에도 같은 화면으로 돌아오게 한다.
  const setTab = useCallback((t: TabKey) => {
    setTabState(t);
    window.history.replaceState(null, "", `#${t}`);
  }, []);

  const load = useCallback(() => {
    api.get<RunDetail>(`/api/runs/${runId}`).then((d) => { setDetail(d); setError(null); }).catch((e) => setError(e.message));
    api.get<Finding[]>(`/api/runs/${runId}/findings`).then(setFindings).catch(() => {});
    api.get<BacklogItem[]>(`/api/runs/${runId}/backlog`).then(setBacklog).catch(() => {});
  }, [runId]);

  useEffect(load, [load]);
  // 실행 중이면 4초, 아니면 15초. 탭이 숨겨져 있으면 멈춘다.
  usePolling(load, detail?.loop_running ? 4000 : 15000);

  const selected = findings?.find((f) => f.id === selectedId) ?? null;
  const closePanel = useCallback(() => setSelectedId(null), []);

  if (error && !detail) return <main className="mx-auto w-full max-w-6xl p-6"><Banner>{error}</Banner></main>;
  if (!detail) return <main className="mx-auto w-full max-w-6xl p-6"><SkeletonRows rows={4} /></main>;

  const approvals = (backlog ?? []).filter(needsApproval).length;
  const counts: Partial<Record<TabKey, number>> = {
    findings: findings?.length,
    backlog: backlog?.length,
    evidence: detail.usage.evidence,
  };

  return (
    <main className="mx-auto w-full max-w-6xl p-6">
      {error && <Banner tone="warn">최신 상태를 가져오지 못했습니다. 화면은 마지막으로 받은 값입니다. ({error})</Banner>}
      <DeletedBanner detail={detail} onChanged={load} />
      <Summary
        detail={detail}
        findings={findings}
        backlog={backlog}
        onFilterFindings={setFilter}
        onShowTab={setTab}
        onChanged={load}
      />

      <div role="tablist" aria-label="진단 상세" className="mb-4 flex gap-1 border-b border-line">
        {TAB_KEYS.map((t) => (
          <button
            key={t}
            role="tab"
            id={`tab-${t}`}
            aria-selected={tab === t}
            aria-controls="tabpanel"
            onClick={() => setTab(t)}
            className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition-colors ${
              tab === t ? "border-accent font-medium text-ink" : "border-transparent text-muted hover:text-ink"
            }`}
          >
            {TAB_LABEL[t]}
            {counts[t] != null && <span className="tabular text-xs text-faint">{counts[t]}</span>}
            {t === "backlog" && approvals > 0 && (
              <span className="rounded-full bg-warn px-1.5 text-[11px] font-semibold text-canvas" title="승인 대기">
                {approvals}
              </span>
            )}
          </button>
        ))}
      </div>

      <div role="tabpanel" id="tabpanel" aria-labelledby={`tab-${tab}`}>
        {tab === "findings" && (
          <FindingsTab runId={runId} findings={findings} filter={filter} setFilter={setFilter} selectedId={selectedId} onSelect={setSelectedId} />
        )}
        {tab === "backlog" && <BacklogTab runId={runId} items={backlog} onChanged={load} />}
        {tab === "assets" && <AssetsTab runId={runId} />}
        {tab === "graph" && <GraphTab runId={runId} />}
        {tab === "traffic" && <TrafficTab runId={runId} />}
        {tab === "chat" && <ChatTab runId={runId} />}
        {tab === "evidence" && <EvidenceTab runId={runId} />}
        {tab === "activity" && <ActivityTab runId={runId} />}
        {tab === "server-log" && <ServerLogTab runId={runId} />}
      </div>

      {selected && <FindingPanel key={selected.id} runId={runId} finding={selected} onClose={closePanel} onChanged={load} />}
    </main>
  );
}

export default function RunDetailPage() {
  const params = useParams<{ id: string }>();
  return (
    <AuthGate>
      <Header />
      <RunDetailBody runId={params.id} />
    </AuthGate>
  );
}
