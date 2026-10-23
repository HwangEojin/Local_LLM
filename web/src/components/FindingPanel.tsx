"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Download, Eye, EyeOff, RotateCw, X } from "lucide-react";

import { StatusBadge } from "@/components/StatusBadge";
import { Banner, btnGhost, btnPrimary, btnSecondary, inputCls, SkeletonRows, useToast } from "@/components/ui";
import { api, ApiError, downloadUrl, Finding, FINDING_STATUSES, FindingDetail } from "@/lib/api";
import { describe, formatBytes, formatTime, VERIFICATION_LABEL } from "@/lib/labels";

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
      <button onClick={toggle} className="inline-flex items-center gap-1 text-xs text-accent hover:underline" aria-expanded={open}>
        {open ? <EyeOff className="h-3.5 w-3.5" aria-hidden /> : <Eye className="h-3.5 w-3.5" aria-hidden />}
        {open ? "미리보기 접기" : "미리보기"}
      </button>
      {open && (
        <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-canvas p-2 font-mono text-[11px] text-muted">
          {error ?? text ?? "불러오는 중…"}
        </pre>
      )}
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
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .get<FindingDetail>(`/api/runs/${runId}/findings/${finding.id}`)
      .then(setDetail)
      .catch((e) => setError(e.message));
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
            <StatusBadge value={f.severity} />
            <StatusBadge value={f.status} />
            <span className="text-xs text-muted">{VERIFICATION_LABEL[f.verification] ?? f.verification}</span>
          </div>
        </div>
        <button onClick={onClose} className={btnGhost} aria-label="패널 닫기 (Esc)" title="닫기 (Esc)">
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto px-5 py-4">
        {error && <Banner>{error}</Banner>}

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
