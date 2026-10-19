"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { AuthGate } from "@/components/AuthGate";
import { Header } from "@/components/Header";
import { StatusBadge } from "@/components/StatusBadge";
import { api, BacklogItem, Finding, FINDING_STATUSES, RunDetail } from "@/lib/api";

function Overview({ detail }: { detail: RunDetail }) {
  const { run, usage } = detail;
  return (
    <div className="mb-6 grid grid-cols-2 gap-4 rounded-lg border border-neutral-200 bg-white p-4 text-sm sm:grid-cols-4">
      <div>
        <div className="text-neutral-500">상태</div>
        <div className="mt-1">
          <StatusBadge value={run.status as string} />
        </div>
      </div>
      <div>
        <div className="text-neutral-500">운영자</div>
        <div className="mt-1">{run.operator as string}</div>
      </div>
      <div>
        <div className="text-neutral-500">자율성</div>
        <div className="mt-1">{run.autonomy as string}</div>
      </div>
      <div>
        <div className="text-neutral-500">증적 / 발견 사항</div>
        <div className="mt-1">
          {usage.evidence} / {usage.findings}
        </div>
      </div>
    </div>
  );
}

function ReviewForm({ finding, runId, onDone }: { finding: Finding; runId: string; onDone: () => void }) {
  const [status, setStatus] = useState(finding.status);
  const [reason, setReason] = useState("");
  const [evidenceIds, setEvidenceIds] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submitReview() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/runs/${runId}/findings/${finding.id}/review`, {
        status,
        reason,
        evidence_ids: evidenceIds
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "검토 기록 실패");
    } finally {
      setBusy(false);
    }
  }

  async function submitRetest() {
    if (!reason) {
      setError("재검증 요청에도 사유(reason)가 필요함");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/runs/${runId}/findings/${finding.id}/retest`, { reason });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "재검증 요청 실패");
    } finally {
      setBusy(false);
    }
  }

  return (
    <tr className="border-t border-neutral-100 bg-neutral-50">
      <td colSpan={6} className="px-4 py-3">
        <div className="mb-2 text-sm text-neutral-700">{finding.rationale}</div>
        <div className="flex flex-wrap items-end gap-2">
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded border border-neutral-300 px-2 py-1 text-sm"
          >
            {FINDING_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <input
            placeholder="사유(reason)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="min-w-48 flex-1 rounded border border-neutral-300 px-2 py-1 text-sm"
          />
          <input
            placeholder="증적 id(쉼표 구분, CONFIRMED 에 필요할 수 있음)"
            value={evidenceIds}
            onChange={(e) => setEvidenceIds(e.target.value)}
            className="min-w-56 flex-1 rounded border border-neutral-300 px-2 py-1 text-sm"
          />
          <button
            disabled={busy || !reason}
            onClick={submitReview}
            className="rounded bg-neutral-900 px-3 py-1 text-sm text-white hover:bg-neutral-700 disabled:opacity-50"
          >
            검토 기록
          </button>
          <button
            disabled={busy}
            onClick={submitRetest}
            className="rounded border border-neutral-300 px-3 py-1 text-sm hover:bg-neutral-100 disabled:opacity-50"
          >
            재검증 요청
          </button>
        </div>
        {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
      </td>
    </tr>
  );
}

function FindingsTab({ runId }: { runId: string }) {
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .get<Finding[]>(`/api/runs/${runId}/findings`)
      .then(setFindings)
      .catch((e) => setError(e.message));
  }, [runId]);

  useEffect(load, [load]);

  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (!findings) return <p className="text-sm text-neutral-500">불러오는 중...</p>;
  if (findings.length === 0) return <p className="text-sm text-neutral-500">발견 사항 없음</p>;

  return (
    <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
      <table className="w-full text-sm">
        <thead className="bg-neutral-50 text-left text-neutral-500">
          <tr>
            <th className="px-4 py-2 font-medium">제목</th>
            <th className="px-4 py-2 font-medium">심각도</th>
            <th className="px-4 py-2 font-medium">검증</th>
            <th className="px-4 py-2 font-medium">상태</th>
            <th className="px-4 py-2 font-medium">검토자</th>
            <th className="px-4 py-2 font-medium" />
          </tr>
        </thead>
        <tbody>
          {findings.map((f) => (
            <Fragment key={f.id}>
              <tr
                className="cursor-pointer border-t border-neutral-100 hover:bg-neutral-50"
                onClick={() => setOpenId(openId === f.id ? null : f.id)}
              >
                <td className="px-4 py-2">{f.title}</td>
                <td className="px-4 py-2">
                  <StatusBadge value={f.severity} />
                </td>
                <td className="px-4 py-2 text-neutral-500">{f.verification}</td>
                <td className="px-4 py-2">
                  <StatusBadge value={f.status} />
                </td>
                <td className="px-4 py-2 text-neutral-500">{f.reviewed_by ?? "검토 전"}</td>
                <td className="px-4 py-2 text-right text-neutral-400">{openId === f.id ? "▲" : "▼"}</td>
              </tr>
              {openId === f.id && (
                <ReviewForm
                  key={`${f.id}-form`}
                  finding={f}
                  runId={runId}
                  onDone={() => {
                    setOpenId(null);
                    load();
                  }}
                />
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BacklogTab({ runId }: { runId: string }) {
  const [items, setItems] = useState<BacklogItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<BacklogItem[]>(`/api/runs/${runId}/backlog`)
      .then(setItems)
      .catch((e) => setError(e.message));
  }, [runId]);

  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (!items) return <p className="text-sm text-neutral-500">불러오는 중...</p>;
  if (items.length === 0) return <p className="text-sm text-neutral-500">작업 없음</p>;

  return (
    <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
      <table className="w-full text-sm">
        <thead className="bg-neutral-50 text-left text-neutral-500">
          <tr>
            <th className="px-4 py-2 font-medium">종류</th>
            <th className="px-4 py-2 font-medium">상태</th>
            <th className="px-4 py-2 font-medium">사유</th>
            <th className="px-4 py-2 font-medium">시도</th>
          </tr>
        </thead>
        <tbody>
          {items.map((it) => (
            <tr key={it.id} className="border-t border-neutral-100">
              <td className="px-4 py-2">{it.kind}</td>
              <td className="px-4 py-2">
                <StatusBadge value={it.status} />
              </td>
              <td className="px-4 py-2 text-neutral-600">{it.blocked_reason ?? it.reason}</td>
              <td className="px-4 py-2 text-neutral-500">{it.attempts}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RunDetailBody({ runId }: { runId: string }) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"findings" | "backlog">("findings");

  useEffect(() => {
    api
      .get<RunDetail>(`/api/runs/${runId}`)
      .then(setDetail)
      .catch((e) => setError(e.message));
  }, [runId]);

  if (error) return <p className="p-6 text-sm text-red-600">{error}</p>;
  if (!detail) return <p className="p-6 text-sm text-neutral-500">불러오는 중...</p>;

  return (
    <div className="p-6">
      <h1 className="mb-4 font-mono text-sm text-neutral-500">{runId}</h1>
      <Overview detail={detail} />
      <div className="mb-3 flex gap-4 border-b border-neutral-200 text-sm">
        {(["findings", "backlog"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`-mb-px border-b-2 px-1 py-2 ${
              tab === t ? "border-neutral-900 font-medium" : "border-transparent text-neutral-500"
            }`}
          >
            {t === "findings" ? "Findings" : "Backlog"}
          </button>
        ))}
      </div>
      {tab === "findings" ? <FindingsTab runId={runId} /> : <BacklogTab runId={runId} />}
    </div>
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
