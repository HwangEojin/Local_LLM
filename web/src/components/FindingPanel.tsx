"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Download, Eye, EyeOff, GitBranch, Pencil, RotateCw, X } from "lucide-react";

import { StatusBadge } from "@/components/StatusBadge";
import { Banner, btnGhost, btnPrimary, btnSecondary, inputCls, SkeletonRows, useToast } from "@/components/ui";
import {
  api,
  ApiError,
  downloadUrl,
  EvidenceProvenance,
  Finding,
  FINDING_STATUSES,
  FindingDetail,
  FindingRevision,
  ProvenanceNodeKind,
  SimilarFinding,
} from "@/lib/api";
import { describe, formatBytes, formatTime, VERIFICATION_LABEL } from "@/lib/labels";

/** observed/inferred 를 색과 텍스트 라벨 둘 다로 구분한다(PRODUCT.md 원칙 1, 색만으로 구분하지 않음). */
function NodeKindTag({ kind }: { kind: ProvenanceNodeKind }) {
  const isObserved = kind === "observed";
  return (
    <span
      className={`rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${
        isObserved ? "border-ok/40 text-ok" : "border-warn/40 text-warn"
      }`}
    >
      {isObserved ? "관찰됨" : "LLM 추론"}
    </span>
  );
}

/** 증거 하나의 출처 체인(G5-a, UPGRADE_PLAN.md) — 목표부터 증거·finding 까지 세로로 보여 준다. */
function ProvenancePanel({ runId, evidenceId }: { runId: string; evidenceId: string }) {
  const [data, setData] = useState<EvidenceProvenance | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<EvidenceProvenance>(`/api/runs/${runId}/evidence/${evidenceId}/provenance`)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "출처를 불러오지 못했습니다"));
  }, [runId, evidenceId]);

  if (error) return <p className="mt-1 text-xs text-danger">{error}</p>;
  if (!data) return <p className="mt-1 text-xs text-faint">불러오는 중…</p>;

  const steps: { label: string; summary: string; kind: ProvenanceNodeKind; detail?: string }[] = [
    ...data.exploration_path.map((n) => ({
      label: n.kind === "goal" ? "목표" : `의도(${n.origin_label})`,
      summary: n.summary || "(요약 없음)",
      kind: n.node_kind,
      detail: n.rationale,
    })),
    ...(data.backlog_item ? [{ label: "작업 사유", summary: data.backlog_item.reason, kind: data.backlog_item.node_kind }] : []),
    ...(data.tool_invocation
      ? [{
          label: "도구 호출",
          summary: `${data.tool_invocation.tool} · 정책 ${data.tool_invocation.policy_decision}`,
          kind: data.tool_invocation.node_kind,
          detail: data.tool_invocation.policy_reason || undefined,
        }]
      : []),
    { label: "증거", summary: `${data.evidence.tool} (${data.evidence.success ? "성공" : "실패"})`, kind: data.evidence.node_kind },
    ...data.findings.map((f) => ({ label: "발견 사항", summary: f.title, kind: f.node_kind, detail: f.verification })),
  ];

  if (steps.length <= 1) {
    return <p className="mt-1 text-xs text-faint">이 증거는 탐색 결정과 연결된 체인이 없습니다(예: 프록시로 받은 트래픽).</p>;
  }

  return (
    <ol className="mt-2 space-y-2 border-l border-line pl-3">
      {steps.map((s, i) => (
        <li key={i} className="text-xs">
          <div className="flex items-center gap-1.5">
            <span className="font-medium text-ink">{s.label}</span>
            <NodeKindTag kind={s.kind} />
          </div>
          <div className="text-muted">{s.summary}</div>
          {s.detail && <div className="mt-0.5 text-faint">{s.detail}</div>}
        </li>
      ))}
    </ol>
  );
}

const EVIDENCE_KIND_LABEL: Record<string, string> = { RULE: "규칙", LLM: "AI" };
const VOTE_STAGE_LABEL: Record<string, string> = { proposer: "제안", verifier: "검증" };
const VOTE_VERDICT_LABEL: Record<string, { label: string; tone: string }> = {
  CONFIRMS: { label: "동의", tone: "text-ok" },
  REFUTES: { label: "반박", tone: "text-danger" },
  INSUFFICIENT: { label: "불충분", tone: "text-warn" },
  ERROR: { label: "오류", tone: "text-danger" },
};

const TEXT_TYPE = /^(text\/|application\/(json|xml))/;

/** 증적 원본을 텍스트로 미리 보여 준다(4,000자까지). 이진 파일은 다운로드로 안내. */
export function EvidenceViewer({
  runId,
  evidenceId,
  mediaType,
  size,
}: {
  runId: string;
  evidenceId: string;
  mediaType: string | null;
  size: number | null;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showProvenance, setShowProvenance] = useState(false);
  const isText = !mediaType || TEXT_TYPE.test(mediaType);

  async function toggle() {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (text !== null || error) return;
    try {
      const res = await fetch(downloadUrl(runId, evidenceId), { credentials: "include" });
      if (!res.ok) throw new Error(`결과를 불러오지 못했습니다 (HTTP ${res.status})`);
      const body = isText ? await res.text() : `[이진 데이터, ${formatBytes(size)}] 다운로드로 확인하세요.`;
      setText(body.length > 4000 ? body.slice(0, 4000) + "\n… (이후 생략)" : body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "결과를 불러오지 못했습니다");
    }
  }

  return (
    <div>
      <div className="flex items-center gap-3">
        <button onClick={toggle} className="inline-flex items-center gap-1 text-xs text-accent hover:underline" aria-expanded={open}>
          {open ? <EyeOff className="h-3.5 w-3.5" aria-hidden /> : <Eye className="h-3.5 w-3.5" aria-hidden />}
          {open ? "미리보기 접기" : "미리보기"}
        </button>
        <button
          onClick={() => setShowProvenance((v) => !v)}
          className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
          aria-expanded={showProvenance}
        >
          <GitBranch className="h-3.5 w-3.5" aria-hidden />
          {showProvenance ? "출처 접기" : "출처 보기"}
        </button>
      </div>
      {open && (
        <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-canvas p-2 font-mono text-[11px] text-muted">
          {error ?? text ?? "불러오는 중…"}
        </pre>
      )}
      {showProvenance && <ProvenancePanel runId={runId} evidenceId={evidenceId} />}
    </div>
  );
}

function ReviewForm({
  detail,
  runId,
  onDone,
}: {
  detail: FindingDetail;
  runId: string;
  onDone: () => void;
}) {
  const f = detail.finding;
  const toast = useToast();
  const [status, setStatus] = useState(f.status);
  const [reason, setReason] = useState("");
  const [picked, setPicked] = useState<string[]>(detail.evidence.map((e) => e.id));
  const [extra, setExtra] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const evidenceIds = [
    ...picked,
    ...extra
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  ];

  async function submitReview() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/runs/${runId}/findings/${f.id}/review`, { status, reason, evidence_ids: evidenceIds });
      toast("ok", `판정을 기록했습니다: ${describe(status).label}`);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "검토 기록에 실패했습니다");
    } finally {
      setBusy(false);
    }
  }

  async function submitRetest() {
    if (!reason) {
      setError("재검증을 요청하려면 사유를 적어야 합니다");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/runs/${runId}/findings/${f.id}/retest`, { reason });
      toast("ok", "재검증을 요청했습니다");
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "재검증 요청에 실패했습니다");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="검토 기록" className="space-y-3">
      <h3 className="text-sm font-semibold">판정 기록</h3>
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <div>
        <label htmlFor="rv-status" className="mb-1 block text-xs text-muted">
          판정
        </label>
        <select id="rv-status" value={status} onChange={(e) => setStatus(e.target.value)} className={inputCls}>
          {FINDING_STATUSES.map((s) => (
            <option key={s} value={s}>
              {describe(s).label}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor="rv-reason" className="mb-1 block text-xs text-muted">
          사유 (필수)
        </label>
        <textarea
          id="rv-reason"
          rows={3}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          className={inputCls}
          placeholder="어떤 증적을 근거로 이렇게 판단했는지 적어 주세요"
        />
      </div>
      <fieldset>
        <legend className="mb-1 text-xs text-muted">근거 증적 (확정 판정에는 필요할 수 있음)</legend>
        {detail.evidence.length === 0 && <p className="text-xs text-faint">연결된 증적이 없습니다.</p>}
        <div className="space-y-1">
          {detail.evidence.map((e) => (
            <label key={e.id} className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                className="accent-[var(--accent)]"
                checked={picked.includes(e.id)}
                onChange={(ev) =>
                  setPicked((p) => (ev.target.checked ? [...p, e.id] : p.filter((x) => x !== e.id)))
                }
              />
              <span className="font-mono">{e.tool}</span>
              <span className="text-faint">{e.id.slice(0, 8)}</span>
            </label>
          ))}
        </div>
        <input
          value={extra}
          onChange={(e) => setExtra(e.target.value)}
          placeholder="다른 증적 ID 직접 입력 (쉼표 구분)"
          className={`${inputCls} mt-2`}
        />
      </fieldset>
      <div className="flex gap-2">
        <button disabled={busy || !reason} onClick={submitReview} className={btnPrimary}>
          <Check className="h-4 w-4" aria-hidden /> 판정 기록
        </button>
        <button disabled={busy} onClick={submitRetest} className={btnSecondary}>
          <RotateCw className="h-4 w-4" aria-hidden /> 재검증 요청
        </button>
      </div>
      <p className="text-xs text-faint">모든 판정은 검토자·시각·사유와 함께 이력으로 보존되며 삭제되지 않습니다.</p>
    </section>
  );
}

/** G3(검토자 편집, UPGRADE_PLAN.md): title/rationale/remediation 만 고칠 수 있다 — 심각도는 개념이
 * 없고(G-S), 영향도는 만들지 않음(D5). 저장하면 바뀐 필드만 이력에 남고(원문 보존), 사람이 고친
 * finding은 보고서 생성 시 Reporter가 다시 다듬지 않는다. */
function EditSection({
  runId,
  finding,
  onDone,
}: {
  runId: string;
  finding: Finding;
  onDone: () => void;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(finding.title);
  const [rationale, setRationale] = useState(finding.rationale);
  const [remediation, setRemediation] = useState(finding.remediation);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (!reason.trim()) {
      setError("수정 사유를 입력하세요");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<{ changed_fields: string[] }>(`/api/runs/${runId}/findings/${finding.id}/edit`, {
        changes: { title, rationale, remediation }, reason,
      });
      toast("ok", r.changed_fields.length > 0 ? `수정했습니다 (${r.changed_fields.join(", ")})` : "바뀐 내용이 없습니다");
      setEditing(false);
      setReason("");
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "수정에 실패했습니다");
    } finally {
      setBusy(false);
    }
  }

  function openEdit() {
    setTitle(finding.title);
    setRationale(finding.rationale);
    setRemediation(finding.remediation);
    setEditing(true);
  }

  if (!editing) {
    return (
      <button onClick={openEdit} className={btnGhost}>
        <Pencil className="h-4 w-4" aria-hidden /> 제목·근거·대응방안 수정
      </button>
    );
  }

  return (
    <section aria-label="발견 사항 수정" className="space-y-2 rounded-md border border-line p-3">
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <div>
        <label className="mb-1 block text-xs text-muted">제목</label>
        <input className={inputCls} value={title} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div>
        <label className="mb-1 block text-xs text-muted">근거(rationale)</label>
        <textarea rows={3} className={inputCls} value={rationale} onChange={(e) => setRationale(e.target.value)} />
      </div>
      <div>
        <label className="mb-1 block text-xs text-muted">대응방안(remediation)</label>
        <textarea rows={2} className={inputCls} value={remediation} onChange={(e) => setRemediation(e.target.value)} />
      </div>
      <div>
        <label className="mb-1 block text-xs text-muted">수정 사유 (필수)</label>
        <input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="왜 이렇게 고치는지" />
      </div>
      <div className="flex gap-2">
        <button disabled={busy} onClick={save} className={btnPrimary}>
          <Check className="h-4 w-4" aria-hidden /> 저장
        </button>
        <button disabled={busy} onClick={() => setEditing(false)} className={btnGhost}>취소</button>
      </div>
      <p className="text-xs text-faint">
        바뀐 필드만 아래 수정 이력에 남고 원문(DB)은 지우지 않습니다. 수정된 finding은 보고서 생성 시
        Reporter가 다시 다듬지 않습니다 — 이 문장이 그대로 보고서에 들어갑니다.
      </p>
    </section>
  );
}

function RevisionHistory({ revisions }: { revisions: FindingRevision[] }) {
  const FIELD_LABEL: Record<string, string> = { title: "제목", rationale: "근거", remediation: "대응방안" };
  if (revisions.length === 0) return null;
  return (
    <section aria-label="수정 이력">
      <h3 className="mb-2 text-sm font-semibold">
        수정 이력 <span className="font-normal text-faint">({revisions.length})</span>
      </h3>
      <ol className="space-y-3 border-l border-line pl-4">
        {revisions.map((r, i) => (
          <li key={i} className="text-sm">
            <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
              <span className="font-medium text-ink">{r.actor}</span>
              <span>·</span>
              <span>{formatTime(r.changed_at)}</span>
              <span className="rounded-full bg-raised px-1.5 py-0.5 text-[10px] font-medium text-muted">
                {FIELD_LABEL[r.field] ?? r.field}
              </span>
            </div>
            <p className="mt-1 text-xs text-faint">사유: {r.reason}</p>
            <p className="mt-1 whitespace-pre-wrap text-muted line-through opacity-60">{r.old_value}</p>
            <p className="mt-1 whitespace-pre-wrap text-ink">{r.new_value}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

function MergeSection({
  runId,
  finding,
  onDone,
}: {
  runId: string;
  finding: Finding;
  onDone: () => void;
}) {
  const toast = useToast();
  const [similar, setSimilar] = useState<SimilarFinding[] | null>(null);
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<SimilarFinding[]>(`/api/runs/${runId}/findings/${finding.id}/similar`).then(setSimilar).catch(() => setSimilar([]));
  }, [runId, finding.id]);

  async function merge() {
    if (!target || !reason) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/runs/${runId}/findings/${finding.id}/merge`, { into: target, reason });
      toast("ok", "다른 발견 사항으로 병합했습니다");
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "병합에 실패했습니다");
    } finally {
      setBusy(false);
    }
  }

  async function unmerge() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/runs/${runId}/findings/${finding.id}/unmerge`, { reason: "사람이 병합 해제" });
      toast("ok", "병합을 해제했습니다");
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "병합 해제에 실패했습니다");
    } finally {
      setBusy(false);
    }
  }

  if (finding.duplicate_of) {
    return (
      <section aria-label="병합 상태" className="space-y-2">
        <h3 className="text-sm font-semibold">병합됨</h3>
        <p className="text-sm text-muted">다른 발견 사항으로 병합된 상태입니다.</p>
        {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
        <button disabled={busy} onClick={unmerge} className={btnSecondary}>병합 해제</button>
      </section>
    );
  }

  const candidates = similar ?? [];

  return (
    <section aria-label="중복 의심·병합" className="space-y-2">
      <h3 className="text-sm font-semibold">
        중복 의심 <span className="font-normal text-faint">({candidates.length})</span>
      </h3>
      {!similar ? (
        <p className="text-xs text-faint">불러오는 중…</p>
      ) : candidates.length === 0 ? (
        <p className="text-xs text-faint">같은 위치(메서드·경로·매개변수)의 다른 발견 사항이 없습니다.</p>
      ) : (
        <>
          {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
          <select value={target} onChange={(e) => setTarget(e.target.value)} className={inputCls}>
            <option value="">— 병합 대상 선택 —</option>
            {candidates.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title} ({describe(s.status).label}, 증적 {s.evidence_count}건)
              </option>
            ))}
          </select>
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="병합 사유 (필수)"
            className={inputCls}
          />
          <button disabled={busy || !target || !reason} onClick={merge} className={btnSecondary}>
            이 finding 으로 병합
          </button>
        </>
      )}
    </section>
  );
}

export function FindingPanel({
  runId,
  finding,
  onClose,
  onChanged,
}: {
  runId: string;
  finding: Finding;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<FindingDetail | null>(null);
  const [revisions, setRevisions] = useState<FindingRevision[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .get<FindingDetail>(`/api/runs/${runId}/findings/${finding.id}`)
      .then(setDetail)
      .catch((e) => setError(e.message));
    api
      .get<FindingRevision[]>(`/api/runs/${runId}/findings/${finding.id}/revisions`)
      .then(setRevisions)
      .catch(() => {});
  }, [runId, finding.id]);

  useEffect(load, [load]); // 다른 finding 으로 바뀌면 부모가 key 로 패널을 새로 만든다

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const f = detail?.finding ?? finding;
  const reopened = detail?.history.find(
    (h) => h.actor === "system:dedup" && h.reason.startsWith("기각 후 새 종류 근거"),
  );

  return (
    <aside
      role="dialog"
      aria-label={`발견 사항 상세: ${f.title}`}
      className="panel-in fixed inset-y-0 right-0 z-40 flex w-full max-w-md flex-col border-l border-line bg-surface shadow-2xl shadow-black/40"
    >
      <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div>
          <h2 className="text-base font-semibold leading-snug">{f.title}</h2>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <StatusBadge value={f.status} />
            <span className="text-xs text-muted">{VERIFICATION_LABEL[f.verification] ?? f.verification}</span>
            {f.human_edited && (
              <span className="rounded-full bg-raised px-2 py-0.5 text-xs font-medium text-muted" title="검토자가 제목·근거·대응방안을 수정함">
                사람이 수정함
              </span>
            )}
          </div>
        </div>
        <button onClick={onClose} className={btnGhost} aria-label="패널 닫기 (Esc)" title="닫기 (Esc)">
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto px-5 py-4">
        {error && <Banner>{error}</Banner>}

        {reopened && (
          <Banner tone="warn">
            <span className="font-medium">기각 후 재관찰됨</span> — {reopened.reason}
          </Banner>
        )}

        <section aria-label="위치·발생 정보" className="space-y-1 text-xs text-muted">
          {f.locator?.method && f.locator?.path && (
            <p>
              <span className="font-mono text-ink">{f.locator.method} {f.locator.path}</span>
              {f.locator.params && f.locator.params.length > 0 && <span> · 매개변수 {f.locator.params.join(", ")}</span>}
            </p>
          )}
          <p>
            발생 {f.occurrence_count}회 · 최근 관찰 {formatTime(f.last_seen_at)}
            {f.evidence_kinds.length > 0 && (
              <> · 근거 종류 {f.evidence_kinds.map((k) => EVIDENCE_KIND_LABEL[k] ?? k).join(", ")}</>
            )}
          </p>
        </section>

        <section aria-label="판단 근거">
          <h3 className="mb-1 text-sm font-semibold">판단 근거</h3>
          <p className="whitespace-pre-wrap text-sm text-muted">{f.rationale || "기록된 근거가 없습니다."}</p>
          {f.remediation && (
            <>
              <h3 className="mb-1 mt-4 text-sm font-semibold">권고 조치</h3>
              <p className="whitespace-pre-wrap text-sm text-muted">{f.remediation}</p>
            </>
          )}
        </section>

        <EditSection runId={runId} finding={f} onDone={() => { load(); onChanged(); }} />

        <RevisionHistory revisions={revisions} />

        <MergeSection runId={runId} finding={f} onDone={() => { load(); onChanged(); }} />

        {!detail && !error ? (
          <SkeletonRows rows={3} />
        ) : detail ? (
          <>
            <section aria-label="연결된 증적">
              <h3 className="mb-2 text-sm font-semibold">
                연결된 증적 <span className="font-normal text-faint">({detail.evidence.length})</span>
              </h3>
              {detail.evidence.length === 0 ? (
                <p className="text-sm text-faint">연결된 증적이 없습니다. 증적 없이 확정할 수 없습니다.</p>
              ) : (
                <ul className="space-y-3">
                  {detail.evidence.map((e) => (
                    <li key={e.id} className="rounded-md border border-line bg-canvas p-3">
                      <div className="flex items-center justify-between gap-2 text-xs">
                        <span className="font-mono text-ink">{e.tool}</span>
                        <span className="text-faint">
                          {formatBytes(e.size)} · {formatTime(e.collected_at)}
                        </span>
                      </div>
                      {e.success ? (
                        <div className="mt-2 flex items-center gap-3">
                          <EvidenceViewer runId={runId} evidenceId={e.id} mediaType={e.media_type} size={e.size} />
                          <a href={downloadUrl(runId, e.id)} className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
                            <Download className="h-3.5 w-3.5" aria-hidden /> 다운로드
                          </a>
                        </div>
                      ) : (
                        <p className="mt-1 text-xs text-danger">수집에 실패한 증적입니다.</p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {detail.llm_votes.length > 0 && (
              <section aria-label="LLM 교차 검증">
                <h3 className="mb-2 text-sm font-semibold">
                  LLM 판정 <span className="font-normal text-faint">({detail.llm_votes.length})</span>
                </h3>
                <ul className="space-y-2">
                  {detail.llm_votes.map((v, i) => (
                    <li key={i} className="rounded-md border border-line bg-canvas p-2 text-xs">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="font-medium text-ink">{VOTE_STAGE_LABEL[v.stage] ?? v.stage}</span>
                        <span className="font-mono text-faint">{v.model}</span>
                        <span className={`font-medium ${VOTE_VERDICT_LABEL[v.verdict]?.tone ?? ""}`}>
                          {VOTE_VERDICT_LABEL[v.verdict]?.label ?? v.verdict}
                        </span>
                        <span className="ml-auto text-faint">{formatTime(v.created_at)}</span>
                      </div>
                      <p className="mt-1 whitespace-pre-wrap text-muted">{v.rationale}</p>
                      {v.evidence_ids.length > 0 && (
                        <p className="mt-1 text-faint">인용 증적 {v.evidence_ids.length}건</p>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <section aria-label="검토 이력">
              <h3 className="mb-2 text-sm font-semibold">
                검토 이력 <span className="font-normal text-faint">({detail.history.length})</span>
              </h3>
              {detail.history.length === 0 ? (
                <p className="text-sm text-faint">아직 검토 이력이 없습니다.</p>
              ) : (
                <ol className="space-y-3 border-l border-line pl-4">
                  {detail.history.map((h, i) => (
                    <li key={i} className="text-sm">
                      <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
                        <span className="font-medium text-ink">{h.actor}</span>
                        <span>·</span>
                        <span>{formatTime(h.changed_at)}</span>
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                        {h.old_status && h.old_status !== h.new_status && (
                          <>
                            <StatusBadge value={h.old_status} />
                            <span className="text-faint">→</span>
                          </>
                        )}
                        <StatusBadge value={h.new_status} />
                      </div>
                      <p className="mt-1 whitespace-pre-wrap text-muted">{h.reason}</p>
                    </li>
                  ))}
                </ol>
              )}
            </section>

            <ReviewForm
              key={`${f.id}-${detail.history.length}`}
              detail={detail}
              runId={runId}
              onDone={() => {
                load();
                onChanged();
              }}
            />
          </>
        ) : null}
      </div>
    </aside>
  );
}
