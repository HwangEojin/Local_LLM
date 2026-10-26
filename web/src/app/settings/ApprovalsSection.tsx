// 승인 구역: 모든 진단의 승인 대기 작업과 최근 결정, 승인 유효 시간. 승인/거부는 진단 화면과 같은 API 를 쓴다
// (승인은 정확한 대상·도구·인자 해시에 묶이고 유효 시간이 지나면 다시 받아야 한다).
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Check, Save, X } from "lucide-react";

import { Banner, btnPrimary, btnSecondary, Card, ConfirmDialog, SkeletonRows, useToast } from "@/components/ui";
import { ApprovalsOverview, api, WorkbenchSettings } from "@/lib/api";
import { formatTime } from "@/lib/labels";
import { errMsg, ICON, NumField } from "./parts";

export function ApprovalsSection() {
  const toast = useToast();
  const [data, setData] = useState<ApprovalsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ttl, setTtl] = useState<number>(0);
  const [confirmTtl, setConfirmTtl] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .get<ApprovalsOverview>("/api/admin/approvals")
      .then((d) => {
        setData(d);
        setTtl(d.ttl_h);
      })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function decide(run: string, id: string, reject: boolean) {
    setBusy(id);
    try {
      await api.post(`/api/runs/${encodeURIComponent(run)}/backlog/${id}/approve`, { reject });
      toast("ok", reject ? "거부했습니다" : "승인했습니다");
    } catch (e) {
      toast("danger", errMsg(e, "처리하지 못했습니다"));
    } finally {
      setBusy(null);
      load(); // 실패해도 목록을 다시 불러 이미 처리된 항목이 남지 않게 한다
    }
  }

  async function saveTtl() {
    setBusy("ttl");
    try {
      // PUT 은 전체 교체라서 현재 값을 읽어 와 승인 유효 시간만 바꿔 보낸다(다른 설정이 초기화되지 않게)
      const w = await api.get<WorkbenchSettings>("/api/admin/workbench");
      await api.put("/api/admin/workbench", {
        exclude: w.exclude,
        lease_s: w.lease_s,
        max_attempts: w.max_attempts,
        approval_ttl_h: ttl,
        min_call_interval_ms: w.min_call_interval_ms,
        budget: w.budget,
        confirm: true,
      });
      toast("ok", "승인 유효 시간을 저장했습니다");
      load();
    } catch (e) {
      setError(errMsg(e, "저장에 실패했습니다"));
    } finally {
      setBusy(null);
      setConfirmTtl(false);
    }
  }

  if (error && !data) return <Banner>{error}</Banner>;
  if (!data) return <SkeletonRows rows={4} />;
  return (
    <div className="space-y-4">
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <Card className="space-y-3 p-4">
        <h2 className="text-sm font-semibold">
          승인 대기 <span className="tabular text-warn">({data.pending.length})</span>
        </h2>
        {data.pending.length === 0 ? (
          <p className="text-sm text-faint">승인을 기다리는 작업이 없습니다.</p>
        ) : (
          <ul className="divide-y divide-line">
            {data.pending.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
                <div className="min-w-0 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link href={`/runs/${p.run_code}`} className="font-mono text-xs text-accent hover:underline">{p.run_code}</Link>
                    <span className="font-mono">{p.tool ?? p.kind}</span>
                    <span className="text-xs text-muted">{p.asset_value}</span>
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-xs text-muted">{p.reason}</p>
                </div>
                <div className="flex shrink-0 gap-2">
                  <button disabled={busy === p.id} onClick={() => decide(p.run_code, p.id, false)} className={btnPrimary}>
                    <Check className={ICON} aria-hidden /> 승인
                  </button>
                  <button disabled={busy === p.id} onClick={() => decide(p.run_code, p.id, true)} className={btnSecondary}>
                    <X className={ICON} aria-hidden /> 거부
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="space-y-3 p-4">
        <h2 className="text-sm font-semibold">승인 유효 시간</h2>
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-48">
            <NumField label="유효 시간 (시간)" min={1} value={ttl} onChange={setTtl} hint="이 시간이 지나면 같은 호출도 다시 승인받아야 합니다" />
          </div>
          <button disabled={busy === "ttl" || ttl === data.ttl_h || !(ttl >= 1)} onClick={() => setConfirmTtl(true)} className={btnPrimary}>
            <Save className={ICON} aria-hidden /> 저장
          </button>
        </div>
      </Card>

      <Card className="space-y-3 p-4">
        <h2 className="text-sm font-semibold">
          최근 결정 <span className="text-xs font-normal text-faint">최근 {data.recent.length}건</span>
        </h2>
        {data.recent.length === 0 ? (
          <p className="text-sm text-faint">아직 승인·거부 기록이 없습니다.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-muted">
              <tr>
                <th scope="col" className="py-1 font-medium">시각</th>
                <th scope="col" className="py-1 font-medium">진단</th>
                <th scope="col" className="py-1 font-medium">도구</th>
                <th scope="col" className="py-1 font-medium">결정</th>
                <th scope="col" className="py-1 font-medium">처리자</th>
              </tr>
            </thead>
            <tbody>
              {data.recent.map((r) => (
                <tr key={r.id} className="border-t border-line">
                  <td className="py-1.5 text-xs">{formatTime(r.decided_at)}</td>
                  <td className="py-1.5">
                    <Link href={`/runs/${r.run_code}`} className="font-mono text-xs text-accent hover:underline">{r.run_code}</Link>
                  </td>
                  <td className="py-1.5 font-mono text-xs">{r.tool}</td>
                  <td className="py-1.5">
                    <span className={r.decision === "APPROVED" ? "text-ok" : "text-danger"}>{r.decision === "APPROVED" ? "승인" : "거부"}</span>
                    {r.decision === "APPROVED" && r.expired && <span className="ml-1 text-xs text-faint">(만료)</span>}
                  </td>
                  <td className="py-1.5 text-xs text-muted">{r.approver}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {confirmTtl && (
        <ConfirmDialog title="승인 유효 시간을 바꿀까요?" confirmLabel="저장" busy={busy === "ttl"} onConfirm={saveTtl} onCancel={() => setConfirmTtl(false)}>
          이후 내리는 승인부터 적용됩니다. 이미 받은 승인의 만료 시각은 바뀌지 않습니다. 변경 전/후 값이 감사 로그에 기록됩니다.
        </ConfirmDialog>
      )}
    </div>
  );
}
