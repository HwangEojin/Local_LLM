// 에이전트 구역: 역할별 프롬프트 조회·수정·기본값 복원. 프롬프트는 LLM 에 주는 문구일 뿐이라 도구 실행 권한·정책은
// 바뀌지 않는다(서버의 policy.decide() 가 매 호출 독립적으로 판정).
import { useCallback, useEffect, useState } from "react";
import { History, RotateCcw, Save } from "lucide-react";

import { Banner, btnGhost, btnPrimary, btnSecondary, Card, ConfirmDialog, inputCls, Sheet, SkeletonRows, useToast } from "@/components/ui";
import { AgentPrompt, AgentPromptVersion, AgentRoleInfo, api } from "@/lib/api";
import { AGENT_ROLE_LABEL, formatTime } from "@/lib/labels";
import { errMsg, ICON } from "./parts";

const ROLE_HELP: Record<string, string> = {
  planner: "관찰 결과를 보고 다음에 무엇을 확인할지 제안합니다(탐색 제안, 최초 정찰).",
  worker: "계획된 작업을 실행하기 직전에 어떤 도구로 확인할지 고릅니다(읽기 도구만).",
  analyst: "체크리스트 항목의 증적을 분석합니다. 이 프롬프트는 코드에 고정돼 있어 편집할 수 없습니다.",
  goals: "운영자가 쓴 진단 목표를 대상별 하위 목표로 나눕니다.",
  reporter: "보고서에 들어가는 설명·권고 문장을 다듬습니다(DB 의 원문은 바뀌지 않음).",
};

function VersionHistory({ name, onClose, onRestored }: { name: string; onClose: () => void; onRestored: () => void }) {
  const toast = useToast();
  const [versions, setVersions] = useState<AgentPromptVersion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<number | null>(null);
  useEffect(() => {
    api.get<AgentPromptVersion[]>(`/api/admin/agents/prompts/${name}/versions`).then(setVersions).catch((e) => setError(e.message));
  }, [name]);

  async function restore(id: number) {
    setRestoring(id);
    try {
      await api.post(`/api/admin/agents/prompts/${name}/versions/${id}/restore`, { confirm: true });
      toast("ok", "그 버전으로 되돌렸습니다");
      onRestored();
      onClose();
    } catch (e) {
      setError(errMsg(e, "복원에 실패했습니다"));
    } finally {
      setRestoring(null);
    }
  }

  return (
    <Sheet title="프롬프트 버전 이력" subtitle={<span className="font-mono">{name}</span>} onClose={onClose}>
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      {!versions ? (
        <SkeletonRows rows={3} />
      ) : versions.length === 0 ? (
        <p className="text-sm text-muted">아직 저장된 버전이 없습니다.</p>
      ) : (
        <ul className="space-y-3">
          {versions.map((v) => (
            <li key={v.id} className="rounded-md border border-line p-3">
              <div className="mb-1 flex items-center justify-between gap-2 text-xs text-faint">
                <span>{v.updated_by} · {formatTime(v.created_at)}</span>
                <button disabled={restoring !== null} onClick={() => restore(v.id)} className={btnSecondary}>
                  {restoring === v.id ? "복원 중…" : "이 버전으로 복원"}
                </button>
              </div>
              <pre className="max-h-32 overflow-y-auto whitespace-pre-wrap font-mono text-xs text-muted">{v.text}</pre>
            </li>
          ))}
        </ul>
      )}
    </Sheet>
  );
}

function PromptEditor({ prompt, onChanged }: { prompt: AgentPrompt; onChanged: () => void }) {
  const toast = useToast();
  const [text, setText] = useState(prompt.text);
  const [confirm, setConfirm] = useState<"save" | "reset" | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setText(prompt.text), [prompt.text]);

  async function run(kind: "save" | "reset") {
    setBusy(true);
    setError(null);
    try {
      if (kind === "save") await api.put(`/api/admin/agents/prompts/${prompt.name}`, { text, confirm: true });
      else await api.post(`/api/admin/agents/prompts/${prompt.name}/reset`, { confirm: true });
      toast("ok", kind === "save" ? "프롬프트를 저장했습니다" : "기본값으로 되돌렸습니다");
      onChanged();
    } catch (e) {
      setError(errMsg(e, "저장에 실패했습니다"));
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{prompt.label}</span>
        <span className="font-mono text-xs text-faint">{prompt.name}</span>
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${prompt.overridden ? "bg-warn/15 text-warn" : "bg-raised text-muted"}`}>
          {prompt.overridden ? "수정됨" : "기본값"}
        </span>
        {prompt.overridden && prompt.updated_at && (
          <span className="text-xs text-faint">{prompt.updated_by} · {formatTime(prompt.updated_at)}</span>
        )}
      </div>
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <textarea rows={8} className={`${inputCls} font-mono text-xs`} value={text} onChange={(e) => setText(e.target.value)} aria-label={`${prompt.label} 프롬프트`} />
      <div className="flex gap-2">
        <button disabled={busy || text.trim() === prompt.text.trim()} onClick={() => setConfirm("save")} className={btnPrimary}>
          <Save className={ICON} aria-hidden /> 저장
        </button>
        <button disabled={busy || !prompt.overridden} onClick={() => setConfirm("reset")} className={btnSecondary}>
          <RotateCcw className={ICON} aria-hidden /> 기본값으로 되돌리기
        </button>
        <button onClick={() => setShowHistory(true)} className={btnGhost}>
          <History className={ICON} aria-hidden /> 버전 이력
        </button>
      </div>
      {showHistory && (
        <VersionHistory name={prompt.name} onClose={() => setShowHistory(false)} onRestored={onChanged} />
      )}
      {confirm && (
        <ConfirmDialog
          title={confirm === "save" ? "프롬프트를 저장할까요?" : "기본값으로 되돌릴까요?"}
          confirmLabel={confirm === "save" ? "저장" : "되돌리기"}
          busy={busy}
          onConfirm={() => run(confirm)}
          onCancel={() => setConfirm(null)}
        >
          이후 새로 하는 LLM 호출부터 적용됩니다. 변경 전/후 문구가 감사 로그에 기록됩니다. 응답 형식(JSON 스키마)은 서버가 강제하므로 프롬프트로 도구 실행 권한이 바뀌지는 않습니다.
        </ConfirmDialog>
      )}
    </div>
  );
}

export function AgentsSection() {
  const [roles, setRoles] = useState<AgentRoleInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api.get<AgentRoleInfo[]>("/api/admin/agents").then(setRoles).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  if (error && !roles) return <Banner>{error}</Banner>;
  if (!roles) return <SkeletonRows rows={4} />;
  return (
    <div className="space-y-4">
      <p className="text-xs text-faint">
        역할마다 쓸 모델은 새 진단을 만들 때 지정합니다(지정하지 않으면 그 진단의 기본 모델). 여기서는 역할이 받는 프롬프트를 관리합니다.
      </p>
      {roles.map((r) => (
        <Card key={r.role} className="space-y-4 p-4">
          <div>
            <h2 className="text-sm font-semibold">
              {AGENT_ROLE_LABEL[r.role] ?? r.role} <span className="ml-1 font-mono text-xs font-normal text-faint">{r.role}</span>
            </h2>
            <p className="mt-0.5 text-xs text-muted">{ROLE_HELP[r.role]}</p>
          </div>
          {r.prompts.map((p) => (
            <PromptEditor key={p.name} prompt={p} onChanged={load} />
          ))}
        </Card>
      ))}
    </div>
  );
}
