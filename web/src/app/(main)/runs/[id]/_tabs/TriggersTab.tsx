"use client";

import { useState } from "react";
import { Trash2, Zap } from "lucide-react";

import { Banner, btnDanger, btnPrimary, btnSecondary, Card, ConfirmDialog, Empty, inputCls, SkeletonRows, useToast } from "@/components/ui";
import { api, ApiError, CustomAgent, Trigger, TRIGGER_KINDS } from "@/lib/api";
import { useRunResource } from "@/lib/useRunResource";
import { AssetRow, parseJsonOr, TRIGGER_KIND_LABEL, useAssets } from "../_shared";

// ── 트리거(F3b-1, UPGRADE_PLAN.md) ──
function NewTriggerForm({ runId, assets, onCreated }: { runId: string; assets: AssetRow[]; onCreated: () => void }) {
  const toast = useToast();
  const { data: agents } = useRunResource<CustomAgent[]>("/api/admin/custom-agents", 0);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<string>(TRIGGER_KINDS[0]);
  const [conditionArgs, setConditionArgs] = useState("{}");
  const [mode, setMode] = useState<"tool" | "agent">("tool");
  const [tool, setTool] = useState("");
  const [args, setArgs] = useState("{}");
  const [customAgentId, setCustomAgentId] = useState("");
  const [assetId, setAssetId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create() {
    const cArgs = parseJsonOr(conditionArgs, {});
    const tArgs = parseJsonOr(args, {});
    if (cArgs === null || tArgs === null) {
      setError("조건 인자/도구 인자는 올바른 JSON 이어야 합니다");
      return;
    }
    if (!name.trim() || !assetId || (mode === "tool" ? !tool.trim() : !customAgentId)) {
      setError(mode === "tool" ? "이름, 대상, 도구를 모두 입력하세요" : "이름, 대상, 에이전트를 모두 고르세요");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/runs/${runId}/triggers`, {
        name: name.trim(), condition_kind: kind, condition_args: cArgs,
        tool: mode === "tool" ? tool.trim() : null, args: mode === "tool" ? tArgs : {},
        custom_agent_id: mode === "agent" ? Number(customAgentId) : null,
        target_asset_id: assetId, confirm: true,
      });
      setName(""); setConditionArgs("{}"); setTool(""); setArgs("{}"); setCustomAgentId(""); setAssetId("");
      toast("ok", "트리거를 만들었습니다");
      onCreated();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "만들지 못했습니다");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="space-y-3 p-4">
      <h3 className="text-sm font-semibold">새 트리거</h3>
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-muted">이름</label>
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="예: 발견되면 포트 재스캔" />
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted">조건</label>
          <select className={inputCls} value={kind} onChange={(e) => setKind(e.target.value)}>
            {TRIGGER_KINDS.map((k) => (
              <option key={k} value={k}>{TRIGGER_KIND_LABEL[k] ?? k}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted">조건 인자(JSON, 예: {"{"}&quot;seconds&quot;:300{"}"})</label>
          <input className={`${inputCls} font-mono`} value={conditionArgs} onChange={(e) => setConditionArgs(e.target.value)} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted">대상</label>
          <select className={inputCls} value={assetId} onChange={(e) => setAssetId(e.target.value)}>
            <option value="">(고르세요)</option>
            {assets.map((a) => (
              <option key={a.id} value={a.id}>{a.kind}:{a.value}</option>
            ))}
          </select>
        </div>
      </div>
      <div className="flex gap-4 text-sm" role="radiogroup" aria-label="담당">
        <label className="flex items-center gap-1.5">
          <input type="radio" name="trigger-mode" checked={mode === "tool"} onChange={() => setMode("tool")} /> 고정 도구
        </label>
        <label className="flex items-center gap-1.5">
          <input type="radio" name="trigger-mode" checked={mode === "agent"} onChange={() => setMode("agent")} /> 커스텀 에이전트
        </label>
      </div>
      {mode === "tool" ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="mb-1 block text-xs text-muted">발동 시 쓸 도구 이름</label>
            <input className={`${inputCls} font-mono`} value={tool} onChange={(e) => setTool(e.target.value)} placeholder="예: net.dns_resolve" />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">도구 인자(JSON)</label>
            <input className={`${inputCls} font-mono`} value={args} onChange={(e) => setArgs(e.target.value)} />
          </div>
        </div>
      ) : (
        <div>
          <label className="mb-1 block text-xs text-muted">담당 에이전트(설정→에이전트에서 먼저 만들어야 함)</label>
          <select className={inputCls} value={customAgentId} onChange={(e) => setCustomAgentId(e.target.value)}>
            <option value="">(고르세요)</option>
            {(agents ?? []).map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
        </div>
      )}
      <p className="text-xs text-faint">
        {mode === "tool"
          ? "조건을 만족하면 LLM 을 거치지 않고 이 도구 호출을 작업 큐에 올립니다 — 그래도 다른 작업과 똑같이 policy.decide() 판정(범위·인터셉트·승인)을 거친 뒤에만 실행됩니다."
          : "조건을 만족하면 이 에이전트의 LLM 이 자기 허용 도구 안에서 tool/args 를 직접 고릅니다 — 고른 뒤에도 다른 작업과 똑같이 policy.decide() 판정을 거친 뒤에만 실행됩니다."}
      </p>
      <button disabled={busy} onClick={create} className={btnPrimary}>
        <Zap className="h-4 w-4" aria-hidden /> 트리거 만들기
      </button>
    </Card>
  );
}

export function TriggersTab({ runId }: { runId: string }) {
  const { data: triggers, reload } = useRunResource<Trigger[]>(`/api/runs/${runId}/triggers`, 10000);
  const { data: assetsData } = useAssets(runId);
  const { data: agents } = useRunResource<CustomAgent[]>("/api/admin/custom-agents", 0);
  const agentName = (id: number | null) => agents?.find((a) => a.id === id)?.name ?? `에이전트 #${id}`;
  const toast = useToast();
  const [confirmDelete, setConfirmDelete] = useState<Trigger | null>(null);
  const [busy, setBusy] = useState(false);

  async function toggle(t: Trigger) {
    setBusy(true);
    try {
      await api.put(`/api/runs/${runId}/triggers/${t.id}`, { enabled: !t.enabled, confirm: true });
      reload();
    } catch (e) {
      toast("danger", e instanceof ApiError ? e.message : "저장에 실패했습니다");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!confirmDelete) return;
    setBusy(true);
    try {
      await api.post(`/api/runs/${runId}/triggers/${confirmDelete.id}/delete`, { confirm: true });
      toast("ok", "트리거를 삭제했습니다");
      reload();
    } catch (e) {
      toast("danger", e instanceof ApiError ? e.message : "삭제에 실패했습니다");
    } finally {
      setBusy(false);
      setConfirmDelete(null);
    }
  }

  return (
    <div className="space-y-4">
      <NewTriggerForm runId={runId} assets={assetsData?.assets ?? []} onCreated={reload} />
      {!triggers ? (
        <SkeletonRows />
      ) : triggers.length === 0 ? (
        <Empty title="등록된 트리거가 없습니다" />
      ) : (
        <Card className="divide-y divide-line">
          {triggers.map((t) => (
            <div key={t.id} className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{t.name}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${t.enabled ? "bg-ok/15 text-ok" : "bg-raised text-muted"}`}>
                    {t.enabled ? "켜짐" : "꺼짐"}
                  </span>
                </div>
                <div className="text-xs text-faint">
                  {TRIGGER_KIND_LABEL[t.condition_kind] ?? t.condition_kind} →{" "}
                  {t.custom_agent_id ? (
                    <span className="font-mono">에이전트: {agentName(t.custom_agent_id)}</span>
                  ) : (
                    <span className="font-mono">{t.tool}</span>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                <button disabled={busy} onClick={() => toggle(t)} className={btnSecondary}>
                  {t.enabled ? "끄기" : "켜기"}
                </button>
                <button disabled={busy} onClick={() => setConfirmDelete(t)} className={btnDanger}>
                  <Trash2 className="h-4 w-4" aria-hidden />
                </button>
              </div>
            </div>
          ))}
        </Card>
      )}
      {confirmDelete && (
        <ConfirmDialog title={`'${confirmDelete.name}' 트리거를 삭제할까요?`} confirmLabel="삭제" danger busy={busy}
          onConfirm={remove} onCancel={() => setConfirmDelete(null)}>
          이 트리거는 더 이상 조건을 평가하지 않습니다. 이미 큐에 올라간 작업에는 영향이 없습니다.
        </ConfirmDialog>
      )}
    </div>
  );
}
