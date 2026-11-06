// 도구 구역: 등록된 도구를 끄거나 켠다. 새 도구 등록·실행 파일·인자 스키마는 웹에서 바꿀 수 없다.
// 위험도(risk)는 분류용 메타데이터일 뿐 승인 게이트가 아니다(CLAUDE.md §8, 2026-11 삭제) — 표시만 하고 편집은 없다.
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
const VISIBILITY_ROLES = ["planner", "worker"]; // 이 둘만 제안 단계에서 도구 후보를 고른다(analyst/goals/reporter 는 해당 없음)

export function ToolsSection() {
  const toast = useToast();
  const [cat, setCat] = useState<ToolCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [risk, setRisk] = useState("");
  const [pending, setPending] = useState<ToolInfo | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get<ToolCatalog>("/api/admin/tools").then(setCat).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const rows = useMemo(
    () => (cat?.tools ?? []).filter((t) => (!risk || t.risk === risk) && t.name.toLowerCase().includes(q.trim().toLowerCase())),
    [cat, q, risk],
  );

  async function apply(t: ToolInfo, disabled: boolean) {
    setBusy(true);
    try {
      setCat(await api.put<ToolCatalog>(`/api/admin/tools/${t.name}`, { disabled, confirm: true }));
      toast("ok", "도구 설정을 저장했습니다");
    } catch (e) {
      setError(errMsg(e, "저장에 실패했습니다"));
    } finally {
      setBusy(false);
      setPending(null);
    }
  }

  async function toggleVisibility(t: ToolInfo, role: string) {
    const hidden_from = t.hidden_from.includes(role) ? t.hidden_from.filter((r) => r !== role) : [...t.hidden_from, role];
    setBusy(true);
    try {
      setCat(await api.put<ToolCatalog>(`/api/admin/tools/${t.name}`, { hidden_from, confirm: true }));
    } catch (e) {
      setError(errMsg(e, "저장에 실패했습니다"));
    } finally {
      setBusy(false);
    }
  }

  if (error && !cat) return <Banner>{error}</Banner>;
  if (!cat) return <SkeletonRows rows={5} />;
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
        <span className="text-xs text-muted">{rows.length} / {cat.tools.length}개</span>
      </div>
      <ul className="divide-y divide-line">
        {rows.map((t) => (
          <li key={t.name} className={`py-2.5 ${t.disabled ? "opacity-60" : ""}`}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="font-mono text-sm">{t.name}</span>
                <span className="text-xs text-faint">{t.source === "mcp" ? "MCP" : "내장"}</span>
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${RISK_CLS[t.risk] ?? ""}`}>{t.risk}</span>
                {t.disabled && <span className="rounded-full bg-raised px-2 py-0.5 text-xs font-medium text-muted">꺼짐</span>}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <button
                  disabled={busy}
                  className={`${t.disabled ? btnPrimary : btnSecondary} whitespace-nowrap`}
                  onClick={() => (t.disabled ? setPending(t) : apply(t, true))}
                >
                  {t.disabled ? <Power className={ICON} aria-hidden /> : <PowerOff className={ICON} aria-hidden />}
                  {t.disabled ? "켜기" : "끄기"}
                </button>
              </div>
            </div>
            <div className="mt-1.5 flex items-center gap-3 text-xs text-muted">
              <span>제안 후보에서 숨기기:</span>
              {VISIBILITY_ROLES.map((role) => (
                <label key={role} className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    className="accent-[var(--accent)]"
                    disabled={busy}
                    checked={t.hidden_from.includes(role)}
                    onChange={() => toggleVisibility(t, role)}
                  />
                  {role}
                </label>
              ))}
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
        끈 도구는 모든 경로에서 &lsquo;등록되지 않은 도구&rsquo;로 거부됩니다(진행 중인 진단은 다음 도구 호출 컨텍스트부터). &ldquo;제안 후보에서 숨기기&rdquo;는
        그 역할의 LLM 제안 목록에서만 안 보이게 할 뿐 도구를 끄는 게 아닙니다 — 다른 경로로 호출되면 여전히 policy.decide() 가 그대로 판정합니다.
        둘 다 이 PC 전용 파일(tools.local.json)에 저장되고 변경 전/후가 감사 로그에 남습니다.
        새 도구 등록, 실행 파일 경로, 인자 스키마, 위험도는 웹에서 바꿀 수 없습니다. 승인 요구 여부는 인터셉트 규칙으로 정합니다.
      </p>

      {pending && (
        <ConfirmDialog
          title={`${pending.name} 도구를 켤까요?`}
          confirmLabel="켜기"
          busy={busy}
          onConfirm={() => apply(pending, false)}
          onCancel={() => setPending(null)}
        >
          이 도구가 다시 진단에서 호출될 수 있게 됩니다. 호출은 여전히 범위·인터셉트 규칙 검사를 통과해야 합니다.
        </ConfirmDialog>
      )}
    </Card>
  );
}
