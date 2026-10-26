// 도구 구역: 등록된 도구를 끄거나 위험도를 바꾼다. 새 도구 등록·실행 파일·인자 스키마는 웹에서 바꿀 수 없다.
// 위험도를 올리는 것은 통제를 강하게 하므로 확인만 받고, 낮추는 것은 승인·범위 통제가 약해지므로 경고와 별도 확인을 받는다.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Power, PowerOff } from "lucide-react";

import { Banner, btnPrimary, btnSecondary, Card, ConfirmDialog, inputCls, SkeletonRows, useToast } from "@/components/ui";
import { api, ToolCatalog, ToolInfo } from "@/lib/api";
import { errMsg, ICON } from "./parts";

const RISK_CLS: Record<string, string> = {
  read: "bg-ok/15 text-ok",
  write: "bg-warn/15 text-warn",
  dangerous: "bg-danger/15 text-danger",
};
const RISKS = ["read", "write", "dangerous"];
const rank = (r: string) => RISKS.indexOf(r);

type Pending = { tool: ToolInfo; kind: "enable" | "risk"; risk?: string };

export function ToolsSection() {
  const toast = useToast();
  const [cat, setCat] = useState<ToolCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [risk, setRisk] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get<ToolCatalog>("/api/admin/tools").then(setCat).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const rows = useMemo(
    () => (cat?.tools ?? []).filter((t) => (!risk || t.risk === risk) && t.name.toLowerCase().includes(q.trim().toLowerCase())),
    [cat, q, risk],
  );

  async function apply(t: ToolInfo, patch: { disabled?: boolean; risk?: string }, lowering = false) {
    setBusy(true);
    try {
      setCat(await api.put<ToolCatalog>(`/api/admin/tools/${t.name}`, { ...patch, confirm: true, confirm_lower_risk: lowering }));
      toast("ok", "도구 설정을 저장했습니다");
    } catch (e) {
      setError(errMsg(e, "저장에 실패했습니다"));
    } finally {
      setBusy(false);
      setPending(null);
    }
  }

  if (error && !cat) return <Banner>{error}</Banner>;
  if (!cat) return <SkeletonRows rows={5} />;
  const lowering = !!pending && pending.kind === "risk" && rank(pending.risk!) < rank(pending.tool.risk);
  return (
    <Card className="space-y-3 p-4">
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <div className="flex flex-wrap items-center gap-3">
        <input className={`${inputCls} max-w-xs`} placeholder="도구 이름 검색" value={q} onChange={(e) => setQ(e.target.value)} aria-label="도구 이름 검색" />
        <select className={`${inputCls} max-w-[10rem]`} value={risk} onChange={(e) => setRisk(e.target.value)} aria-label="위험도 필터">
          <option value="">모든 위험도</option>
          {RISKS.map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </select>
        <span className="text-xs text-muted">
          {rows.length} / {cat.tools.length}개 · 현재 범위가 허용하는 위험도: {cat.allowed_risks.join(", ") || "없음"}
        </span>
      </div>
      <ul className="divide-y divide-line">
        {rows.map((t) => (
          <li key={t.name} className={`py-2.5 ${t.disabled ? "opacity-60" : ""}`}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="font-mono text-sm">{t.name}</span>
                <span className="text-xs text-faint">{t.source === "mcp" ? "MCP" : "내장"}</span>
                {t.disabled && <span className="rounded-full bg-raised px-2 py-0.5 text-xs font-medium text-muted">꺼짐</span>}
                {t.risk !== t.base_risk && <span className="text-xs text-warn">기본 {t.base_risk} → {t.risk}</span>}
                {!t.allowed_by_scope && !t.disabled && <span className="text-xs text-warn">현재 범위가 허용하지 않는 위험도</span>}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <select
                  className={`${inputCls} w-28 ${RISK_CLS[t.risk] ?? ""}`}
                  value={t.risk}
                  disabled={busy}
                  aria-label={`${t.name} 위험도`}
                  onChange={(e) => setPending({ tool: t, kind: "risk", risk: e.target.value })}
                >
                  {RISKS.map((r) => (
                    <option key={r} value={r}>{r}</option>
                  ))}
                </select>
                <button
                  disabled={busy}
                  className={`${t.disabled ? btnPrimary : btnSecondary} whitespace-nowrap`}
                  onClick={() => (t.disabled ? setPending({ tool: t, kind: "enable" }) : apply(t, { disabled: true }))}
                >
                  {t.disabled ? <Power className={ICON} aria-hidden /> : <PowerOff className={ICON} aria-hidden />}
                  {t.disabled ? "켜기" : "끄기"}
                </button>
              </div>
            </div>
            <details className="mt-1">
              <summary className="cursor-pointer text-xs text-muted">인자 스키마 · 범위 검사 인자 {Object.keys(t.scope_args).length}개</summary>
              <pre className="mt-1 max-h-48 overflow-auto rounded bg-canvas p-2 text-xs">{JSON.stringify({ args_schema: t.args_schema, scope_args: t.scope_args }, null, 2)}</pre>
            </details>
          </li>
        ))}
        {rows.length === 0 && <li className="py-3 text-sm text-faint">조건에 맞는 도구가 없습니다.</li>}
      </ul>
      <p className="text-xs text-faint">
        끈 도구는 모든 경로에서 &lsquo;등록되지 않은 도구&rsquo;로 거부됩니다(진행 중인 진단은 다음 도구 호출 컨텍스트부터). 이 PC 전용 파일(tools.local.json)에 저장되고 변경 전/후가 감사 로그에 남습니다.
        새 도구 등록, 실행 파일 경로, 인자 스키마는 웹에서 바꿀 수 없습니다.
      </p>

      {pending && (
        <ConfirmDialog
          title={pending.kind === "enable" ? `${pending.tool.name} 도구를 켤까요?` : `${pending.tool.name} 위험도를 ${pending.risk} 로 바꿀까요?`}
          confirmLabel={pending.kind === "enable" ? "켜기" : "변경"}
          danger={lowering}
          busy={busy}
          onConfirm={() => (pending.kind === "enable" ? apply(pending.tool, { disabled: false }) : apply(pending.tool, { risk: pending.risk }, lowering))}
          onCancel={() => setPending(null)}
        >
          {pending.kind === "enable" && "이 도구가 다시 진단에서 호출될 수 있게 됩니다. 호출은 여전히 범위·위험도·승인 검사를 통과해야 합니다."}
          {pending.kind === "risk" && !lowering && "위험도를 올리면 승인·범위 통제가 더 엄격하게 적용됩니다."}
          {lowering && "위험도를 낮추면 이 도구에 적용되는 승인·범위 통제가 약해집니다(예: write → read 는 승인 없이 실행). 정말 낮출 도구인지 확인하세요."}
        </ConfirmDialog>
      )}
    </Card>
  );
}
