"use client";

// 실행 간 비교(G6, UPGRADE_PLAN.md) — 신규/지속/재발/미재현(조치 추정)/미확인/비교 불가. 새 진단 로직 없음:
// /api/compare(global_views.py, compare.compare_runs)만 읽는다. "관찰되지 않음"을 "고쳐짐"의 증거로
// 단정하지 않으므로 미재현(조치 추정)과 미확인을 화면에서도 분리해 보여 준다(CLAUDE.md §15).
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { Banner, btnPrimary, Card, Empty, inputCls } from "@/components/ui";
import { ApiError, api, Finding, Run, RunCompareResult, RunList } from "@/lib/api";
import { formatTime } from "@/lib/labels";

function FindingRow({ f, run_code }: { f: Finding; run_code?: string }) {
  return (
    <div className="flex items-center justify-between gap-2 p-2 text-sm">
      <span className="font-medium">{f.title}</span>
      <div className="flex items-center gap-2">
        {run_code && (
          <Link href={`/runs/${run_code}`} className="font-mono text-xs text-accent hover:underline">
            {run_code}
          </Link>
        )}
        <StatusBadge value={f.status} />
        <span className="text-xs text-faint">{formatTime(f.last_seen_at)}</span>
      </div>
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-1 text-sm font-semibold">{title}</h2>
      {hint && <p className="mb-2 text-xs text-faint">{hint}</p>}
      {children}
    </section>
  );
}

export default function RunComparePage() {
  const params = useSearchParams();
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [base, setBase] = useState(params.get("base") ?? "");
  const [head, setHead] = useState(params.get("head") ?? "");
  const [result, setResult] = useState<RunCompareResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<RunList>("/api/runs?limit=200").then((r) => setRuns(r.runs)).catch(() => {});
  }, []);

  const compare = useCallback(() => {
    if (!base || !head) return;
    setBusy(true);
    setError(null);
    setResult(null);
    api
      .get<RunCompareResult>(`/api/compare?base=${encodeURIComponent(base)}&head=${encodeURIComponent(head)}`)
      .then(setResult)
      .catch((e) => setError(e instanceof ApiError ? e.message : "비교에 실패했습니다"))
      .finally(() => setBusy(false));
  }, [base, head]);

  // URL 에 base/head 가 이미 있으면(자산 상세의 "이 자산의 실행 비교" 바로가기) 바로 비교를 돌린다.
  useEffect(() => {
    if (params.get("base") && params.get("head")) compare();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <main className="mx-auto w-full max-w-5xl space-y-6 p-6">
      <div>
        <h1 className="text-lg font-semibold">실행 비교</h1>
        <p className="text-sm text-muted">두 실행의 finding 을 fingerprint 로 맞춰 신규·지속·재발·미재현·미확인으로 나눕니다.</p>
      </div>

      <Card className="flex flex-wrap items-end gap-3 p-4">
        <div>
          <label className="mb-1 block text-xs text-muted">기준(base, 이전 실행)</label>
          <select value={base} onChange={(e) => setBase(e.target.value)} className={inputCls}>
            <option value="">(고르세요)</option>
            {(runs ?? []).map((r) => (
              <option key={r.id} value={r.run_code}>{r.run_code}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted">대상(head, 이후 실행)</label>
          <select value={head} onChange={(e) => setHead(e.target.value)} className={inputCls}>
            <option value="">(고르세요)</option>
            {(runs ?? []).map((r) => (
              <option key={r.id} value={r.run_code}>{r.run_code}</option>
            ))}
          </select>
        </div>
        <button disabled={!base || !head || busy} onClick={compare} className={btnPrimary}>
          비교
        </button>
      </Card>

      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}

      {result && (
        <div className="space-y-6">
          <p className="text-xs text-faint">
            {result.base_run.run_code} → {result.head_run.run_code}
          </p>

          <Section title={`신규 (${result.new.length})`} hint="head 에만 있는 finding.">
            {result.new.length === 0 ? <Empty title="신규 finding 이 없습니다" /> : (
              <Card className="divide-y divide-line">
                {result.new.map((f) => <FindingRow key={f.id} f={f} run_code={result.head_run.run_code} />)}
              </Card>
            )}
          </Section>

          <Section title={`재발 (${result.recurred.length})`} hint="base 에서 FIXED 재검증 이력이 있었는데 head 에서 다시 관찰됨.">
            {result.recurred.length === 0 ? <Empty title="재발한 finding 이 없습니다" /> : (
              <Card className="divide-y divide-line">
                {result.recurred.map((p) => <FindingRow key={p.head.id} f={p.head} run_code={result.head_run.run_code} />)}
              </Card>
            )}
          </Section>

          <Section title={`지속 (${result.persistent.length})`} hint="양쪽 실행 모두에 있음.">
            {result.persistent.length === 0 ? <Empty title="지속되는 finding 이 없습니다" /> : (
              <Card className="divide-y divide-line">
                {result.persistent.map((p) => <FindingRow key={p.head.id} f={p.head} run_code={result.head_run.run_code} />)}
              </Card>
            )}
          </Section>

          <Section
            title={`미재현 — 조치 추정 (${result.mitigated_presumed.length})`}
            hint="head 가 같은 지점을 실제로 다시 때렸는데 신호가 안 뜨거나, FIXED 재검증 이력이 있음. 확정은 아님 — 사람이 확인해야 합니다."
          >
            {result.mitigated_presumed.length === 0 ? <Empty title="조치 추정 finding 이 없습니다" /> : (
              <Card className="divide-y divide-line">
                {result.mitigated_presumed.map((f) => <FindingRow key={f.id} f={f} run_code={result.base_run.run_code} />)}
              </Card>
            )}
          </Section>

          <Section
            title={`이번 실행에서 미확인 (${result.unconfirmed.length})`}
            hint="head 가 이 지점을 아예 다시 때리지 않음 — 고쳐졌다는 증거가 없습니다(관찰되지 않음 ≠ 조치됨)."
          >
            {result.unconfirmed.length === 0 ? <Empty title="미확인 finding 이 없습니다" /> : (
              <Card className="divide-y divide-line">
                {result.unconfirmed.map((f) => <FindingRow key={f.id} f={f} run_code={result.base_run.run_code} />)}
              </Card>
            )}
          </Section>

          {result.incomparable.length > 0 && (
            <Section title={`비교 불가 (${result.incomparable.length})`} hint="위치(method/path/params)를 못 뽑아 fingerprint 가 없는 과거 finding — 자동 분류하지 않습니다.">
              <Card className="divide-y divide-line">
                {result.incomparable.map((f) => <FindingRow key={f.id} f={f} />)}
              </Card>
            </Section>
          )}
        </div>
      )}
    </main>
  );
}
