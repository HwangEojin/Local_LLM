// 에이전트 구역: 역할별 프롬프트 조회·수정·기본값 복원. 프롬프트는 LLM 에 주는 문구일 뿐이라 도구 실행 권한·정책은
// 바뀌지 않는다(서버의 policy.decide() 가 매 호출 독립적으로 판정).
import { useCallback, useEffect, useState } from "react";
import { Bot, History, Plus, RotateCcw, Save, Trash2 } from "lucide-react";

import { Banner, btnGhost, btnPrimary, btnSecondary, Card, ConfirmDialog, inputCls, Sheet, SkeletonRows, useToast } from "@/components/ui";
import {
  AgentPrompt,
  AgentProfile,
  AgentPromptVersion,
  AgentRoleDefaults,
  AgentRoleInfo,
  api,
  ApiError,
  CustomAgent,
  LlmProfileRef,
  Skill,
  SkillsResponse,
  ToolCatalog,
  ToolInfo,
} from "@/lib/api";
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

function RoleLlmDefault({ role }: { role: string }) {
  const toast = useToast();
  const [llmProfiles, setLlmProfiles] = useState<LlmProfileRef[]>([]);
  const [defaults, setDefaults] = useState<AgentRoleDefaults | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<{ profiles: LlmProfileRef[] }>("/api/environment/ollama-models").then((r) => setLlmProfiles(r.profiles ?? [])).catch(() => {});
    api.get<AgentRoleDefaults>("/api/admin/agents/role-defaults").then(setDefaults).catch((e) => setError(e.message));
  }, []);

  async function change(llm_profile: string) {
    setBusy(true);
    try {
      await api.put(`/api/admin/agents/role-defaults/${role}`, { llm_profile: llm_profile || null, confirm: true });
      setDefaults((d) => ({ ...d, [role]: llm_profile }));
      toast("ok", "기본 모델을 저장했습니다");
    } catch (e) {
      setError(errMsg(e, "저장에 실패했습니다"));
    } finally {
      setBusy(false);
    }
  }

  if (!defaults) return <SkeletonRows rows={1} />;
  return (
    <div className="grid gap-1.5 rounded-md border border-line p-3">
      <label htmlFor="rd" className="text-xs text-muted">
        기본 모델 — 새 진단을 만들 때 &ldquo;에이전트별 모델&rdquo; 칸을 이 값으로 미리 채웁니다(실행 중인 진단엔 영향 없음)
      </label>
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      {llmProfiles.length === 0 ? (
        <p className="text-xs text-faint">등록된 LLM 프로파일이 없습니다(config/llm_profiles.json).</p>
      ) : (
        <select id="rd" disabled={busy} className={`${inputCls} max-w-xs`} value={defaults[role] ?? ""}
          onChange={(e) => change(e.target.value)}>
          <option value="">(지정 안 함)</option>
          {llmProfiles.map((p) => (
            <option key={p.name} value={p.name}>{p.name}</option>
          ))}
        </select>
      )}
    </div>
  );
}

function NewProfileForm({ role, prompts, onCreated, onCancel }: {
  role: string; prompts: AgentPrompt[]; onCreated: () => void; onCancel: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [llmProfile, setLlmProfile] = useState("");
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create() {
    if (!name.trim()) {
      setError("이름을 입력하세요");
      return;
    }
    const prompt_overrides = Object.fromEntries(Object.entries(texts).filter(([, v]) => v.trim()));
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/admin/agents/profiles/${role}`, {
        name: name.trim(), prompt_overrides, llm_profile: llmProfile.trim() || null, confirm: true,
      });
      toast("ok", "프로파일을 만들었습니다");
      onCreated();
    } catch (e) {
      setError(errMsg(e, "만들지 못했습니다"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet title={`새 프로파일 — ${AGENT_ROLE_LABEL[role] ?? role}`} onClose={onCancel}>
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <div className="space-y-3">
        <div>
          <label className="mb-1 block text-xs text-muted">이름 (예: worker-aggressive)</label>
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted">기본 LLM 프로파일(선택, 비우면 실행 생성 시 고른 모델을 그대로 씀)</label>
          <input className={`${inputCls} font-mono`} value={llmProfile} onChange={(e) => setLlmProfile(e.target.value)}
            placeholder="config/llm_profiles.json 의 프로파일 이름" />
        </div>
        {prompts.map((p) => (
          <div key={p.name}>
            <label className="mb-1 block text-xs text-muted">{p.label}(선택, 비우면 전역 설정/기본값을 그대로 씀)</label>
            <textarea rows={4} className={`${inputCls} font-mono text-xs`} value={texts[p.name] ?? ""}
              onChange={(e) => setTexts((t) => ({ ...t, [p.name]: e.target.value }))} />
          </div>
        ))}
        <button disabled={busy} onClick={create} className={btnPrimary}>
          <Plus className={ICON} aria-hidden /> 만들기
        </button>
      </div>
    </Sheet>
  );
}

function RoleProfiles({ role, prompts, profiles, onChanged }: {
  role: string; prompts: AgentPrompt[]; profiles: AgentProfile[]; onChanged: () => void;
}) {
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<AgentProfile | null>(null);
  const [busy, setBusy] = useState(false);

  async function remove() {
    if (!confirmDelete) return;
    setBusy(true);
    try {
      await api.post(`/api/admin/agents/profiles/${confirmDelete.id}/delete`, { confirm: true });
      toast("ok", "프로파일을 삭제했습니다");
      onChanged();
    } catch (e) {
      toast("danger", errMsg(e, "삭제에 실패했습니다"));
    } finally {
      setBusy(false);
      setConfirmDelete(null);
    }
  }

  return (
    <div className="space-y-2 border-t border-line pt-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-medium text-muted">이름 있는 프로파일</h3>
        <button onClick={() => setAdding(true)} className={btnGhost}>
          <Plus className={ICON} aria-hidden /> 추가
        </button>
      </div>
      {profiles.length === 0 ? (
        <p className="text-xs text-faint">없음 — 새 진단을 만들 때는 전역 설정/기본 모델이 그대로 쓰입니다.</p>
      ) : (
        <ul className="space-y-1">
          {profiles.map((p) => (
            <li key={p.id} className="flex items-center justify-between gap-2 rounded-md border border-line px-2.5 py-1.5 text-xs">
              <span>
                <span className="font-medium">{p.name}</span>
                {p.llm_profile && <span className="ml-2 font-mono text-faint">모델: {p.llm_profile}</span>}
                <span className="ml-2 text-faint">프롬프트 재정의 {Object.keys(p.prompt_overrides).length}개</span>
              </span>
              <button onClick={() => setConfirmDelete(p)} className={btnGhost} aria-label={`${p.name} 삭제`}>
                <Trash2 className={ICON} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
      {adding && (
        <NewProfileForm role={role} prompts={prompts} onCancel={() => setAdding(false)}
          onCreated={() => { setAdding(false); onChanged(); }} />
      )}
      {confirmDelete && (
        <ConfirmDialog title={`'${confirmDelete.name}' 프로파일을 삭제할까요?`} confirmLabel="삭제" danger busy={busy}
          onConfirm={remove} onCancel={() => setConfirmDelete(null)}>
          이 프로파일을 고른 새 진단은 더 이상 만들 수 없습니다. 이미 시작된 진단에는 영향이 없습니다.
        </ConfirmDialog>
      )}
    </div>
  );
}

function RoleVisibility({ role }: { role: string }) {
  const toast = useToast();
  const [skills, setSkills] = useState<SkillsResponse | null>(null);
  const [tools, setTools] = useState<ToolInfo[] | null>(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<SkillsResponse>("/api/admin/skills").then(setSkills).catch((e) => setError(e.message));
    api.get<ToolCatalog>("/api/admin/tools")
      .then((c) => setTools(c.tools))  // 위험도 분류 삭제(2026-11) — 모든 도구가 LLM 제안 후보가 될 수 있음
      .catch((e) => setError(e.message));
  }, []);

  async function toggleSkill(s: Skill) {
    const included = s.roles.includes(role);
    const roles = included ? s.roles.filter((r) => r !== role) : [...s.roles, role];
    setBusy(s.name);
    try {
      await api.put(`/api/admin/skills/${encodeURIComponent(s.name)}`,
        { description: s.description, license: s.license, instructions: s.instructions, roles, enabled: s.enabled, confirm: true });
      setSkills((d) => d && { ...d, skills: d.skills.map((x) => (x.name === s.name ? { ...x, roles } : x)) });
    } catch (e) {
      toast("danger", errMsg(e, "저장에 실패했습니다"));
    } finally {
      setBusy(null);
    }
  }

  async function toggleTool(t: ToolInfo) {
    const hidden = t.hidden_from.includes(role);
    const hidden_from = hidden ? t.hidden_from.filter((r) => r !== role) : [...t.hidden_from, role];
    setBusy(t.name);
    try {
      await api.put(`/api/admin/tools/${t.name}`, { hidden_from, confirm: true });
      setTools((xs) => xs && xs.map((x) => (x.name === t.name ? { ...x, hidden_from } : x)));
    } catch (e) {
      toast("danger", errMsg(e, "저장에 실패했습니다"));
    } finally {
      setBusy(null);
    }
  }

  const filteredTools = (tools ?? []).filter((t) => t.name.toLowerCase().includes(q.trim().toLowerCase()));

  return (
    <div className="space-y-5">
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <div>
        <h3 className="mb-1 text-xs font-medium text-muted">스킬 — 체크하면 이 역할의 프롬프트에 지침이 덧붙습니다</h3>
        {!skills ? <SkeletonRows rows={2} /> : skills.skills.length === 0 ? (
          <p className="text-xs text-faint">등록된 스킬이 없습니다.</p>
        ) : (
          <ul className="divide-y divide-line rounded-md border border-line">
            {skills.skills.map((s) => (
              <li key={s.name} className="flex items-center gap-2 px-2.5 py-1.5 text-xs">
                <input type="checkbox" className="accent-[var(--accent)]" disabled={busy === s.name}
                  checked={s.roles.includes(role)} onChange={() => toggleSkill(s)} />
                <span className="font-medium">{s.name}</span>
                {!s.enabled && <span className="text-faint">(꺼짐)</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <div className="mb-1 flex items-center justify-between gap-2">
          <h3 className="text-xs font-medium text-muted">도구 — 체크하면 이 역할의 제안 후보에서 숨깁니다(실행 권한과 무관)</h3>
          <input className={`${inputCls} h-7 max-w-[10rem] text-xs`} placeholder="검색" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {!tools ? <SkeletonRows rows={3} /> : filteredTools.length === 0 ? (
          <p className="text-xs text-faint">조건에 맞는 도구가 없습니다.</p>
        ) : (
          <ul className="max-h-72 divide-y divide-line overflow-y-auto rounded-md border border-line">
            {filteredTools.map((t) => (
              <li key={t.name} className="flex items-center gap-2 px-2.5 py-1.5 text-xs">
                <input type="checkbox" className="accent-[var(--accent)]" disabled={busy === t.name}
                  checked={t.hidden_from.includes(role)} onChange={() => toggleTool(t)} />
                <span className="truncate font-mono">{t.name}</span>
                {t.disabled && <span className="shrink-0 text-faint">(꺼짐)</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function AgentCard({ role, prompts, profileCount, skillCount, onOpen }: {
  role: string; prompts: AgentPrompt[]; profileCount: number; skillCount: number; onOpen: () => void;
}) {
  return (
    <button onClick={onOpen} className="text-left">
      <Card className="space-y-2 p-3 transition-colors hover:border-accent/50">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 truncate text-sm font-medium">
            <Bot className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden /> {AGENT_ROLE_LABEL[role] ?? role}
            <span className="font-mono text-xs font-normal text-faint">{role}</span>
          </span>
          <span className="shrink-0 rounded-full bg-raised px-2 py-0.5 text-xs font-medium text-muted">고정 역할</span>
        </div>
        <p className="line-clamp-2 min-h-8 text-xs text-muted">{ROLE_HELP[role]}</p>
        <div className="flex flex-wrap gap-1.5 text-[10px] text-faint">
          <span className="rounded border border-line px-1.5 py-0.5">프롬프트 {prompts.length}</span>
          <span className="rounded border border-line px-1.5 py-0.5">프로파일 {profileCount}</span>
          <span className="rounded border border-line px-1.5 py-0.5">스킬 {skillCount}</span>
        </div>
      </Card>
    </button>
  );
}

const VISIBILITY_TAB_ROLES = new Set(["planner", "worker"]);  // 이 둘만 제안 단계에서 도구 후보를 고른다

function AgentDetailSheet({ role, prompts, profiles, skillCount, onClose, onChanged }: {
  role: string; prompts: AgentPrompt[]; profiles: AgentProfile[]; skillCount: number;
  onClose: () => void; onChanged: () => void;
}) {
  const tabs = ["prompt", "profiles", ...(VISIBILITY_TAB_ROLES.has(role) ? ["visibility"] : [])] as const;
  const [tab, setTab] = useState<(typeof tabs)[number]>("prompt");
  return (
    <Sheet title={AGENT_ROLE_LABEL[role] ?? role} subtitle={<span className="font-mono">{role}</span>} onClose={onClose} widthClass="max-w-lg">
      <div className="mb-4 flex gap-1.5 border-b border-line pb-3">
        {tabs.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`rounded-full px-3 py-1 text-xs ${tab === t ? "bg-accent/10 text-accent" : "text-muted hover:text-ink"}`}>
            {t === "prompt" ? "프롬프트" : t === "profiles" ? `프로파일 (${profiles.length})` : `가시성 (스킬 ${skillCount})`}
          </button>
        ))}
      </div>
      {tab === "prompt" && (
        <div className="space-y-4">
          <RoleLlmDefault role={role} />
          {prompts.map((p) => (
            <PromptEditor key={p.name} prompt={p} onChanged={onChanged} />
          ))}
        </div>
      )}
      {tab === "profiles" && <RoleProfiles role={role} prompts={prompts} profiles={profiles} onChanged={onChanged} />}
      {tab === "visibility" && <RoleVisibility role={role} />}
    </Sheet>
  );
}

// ── 커스텀 에이전트(레퍼런스 프로젝트 스타일 자유 에이전트 생성, 2026-11-13) ──
// 5개 고정 역할과 별개. 자기 실행 경로는 없다 — 진단 상세의 "트리거" 탭에서 트리거의 담당으로 고르면
// 그 트리거가 발동할 때만 이 정의(프롬프트·모델·허용 도구)로 LLM 이 tool/args 를 고른다.

function CustomAgentCard({ agent, onOpen }: { agent: CustomAgent; onOpen: () => void }) {
  return (
    <button onClick={onOpen} className="text-left">
      <Card className="space-y-2 p-3 transition-colors hover:border-accent/50">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 truncate text-sm font-medium">
            <Bot className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden /> {agent.name}
          </span>
          {!agent.enabled && <span className="shrink-0 rounded-full bg-raised px-2 py-0.5 text-xs font-medium text-muted">꺼짐</span>}
        </div>
        <p className="line-clamp-2 min-h-8 text-xs text-muted">{agent.system_prompt}</p>
        <div className="flex flex-wrap gap-1.5 text-[10px] text-faint">
          <span className="rounded border border-line px-1.5 py-0.5">허용 도구 {agent.allowed_tools.length}</span>
          {agent.llm_profile && <span className="rounded border border-line px-1.5 py-0.5 font-mono">{agent.llm_profile}</span>}
        </div>
      </Card>
    </button>
  );
}

function NewCustomAgentCard({ onOpen }: { onOpen: () => void }) {
  return (
    <button onClick={onOpen} className="text-left">
      <Card className="flex min-h-[7.5rem] items-center justify-center gap-1.5 border-dashed p-3 text-sm text-muted transition-colors hover:border-accent/50 hover:text-ink">
        <Plus className="h-4 w-4" aria-hidden /> 에이전트 만들기
      </Card>
    </button>
  );
}

function CustomAgentSheet({ agent, tools, onClose, onChanged }: {
  agent: CustomAgent | null; tools: ToolInfo[]; onClose: () => void; onChanged: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState(agent?.name ?? "");
  const [systemPrompt, setSystemPrompt] = useState(agent?.system_prompt ?? "");
  const [llmProfile, setLlmProfile] = useState(agent?.llm_profile ?? "");
  const [allowedTools, setAllowedTools] = useState<string[]>(agent?.allowed_tools ?? []);
  const [enabled, setEnabled] = useState(agent?.enabled ?? true);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  function toggleTool(name: string) {
    setAllowedTools((xs) => (xs.includes(name) ? xs.filter((x) => x !== name) : [...xs, name]));
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const body = { name: name.trim(), system_prompt: systemPrompt.trim(), llm_profile: llmProfile.trim() || null,
                     allowed_tools: allowedTools, enabled, confirm: true };
      if (agent) await api.put(`/api/admin/custom-agents/${agent.id}`, body);
      else await api.post("/api/admin/custom-agents", body);
      toast("ok", agent ? "저장했습니다" : "에이전트를 만들었습니다");
      onChanged();
      onClose();
    } catch (e) {
      setError(errMsg(e, "저장에 실패했습니다"));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!agent) return;
    setBusy(true);
    try {
      await api.post(`/api/admin/custom-agents/${agent.id}/delete`, { confirm: true });
      toast("ok", "에이전트를 삭제했습니다");
      onChanged();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "삭제에 실패했습니다");
      setConfirmDelete(false);
    } finally {
      setBusy(false);
    }
  }

  const filteredTools = tools.filter((t) => t.name.toLowerCase().includes(q.trim().toLowerCase()));

  return (
    <Sheet title={agent ? agent.name : "새 커스텀 에이전트"} onClose={onClose}>
      <div className="space-y-4">
        {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
        <div>
          <label className="mb-1 block text-xs text-muted">이름</label>
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="예: 포트 재스캔 에이전트" />
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted">시스템 프롬프트(20자 이상)</label>
          <textarea className={`${inputCls} h-32`} value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted">LLM 프로파일(비우면 이 실행의 기본 모델)</label>
          <input className={`${inputCls} font-mono`} value={llmProfile} onChange={(e) => setLlmProfile(e.target.value)}
            placeholder="예: ollama:qwen2.5:7b-instruct 또는 프로파일 이름" />
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="accent-[var(--accent)]" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          켜짐
        </label>
        <div>
          <div className="mb-1 flex items-center justify-between gap-2">
            <h3 className="text-xs font-medium text-muted">허용 도구(이 안에서만 고를 수 있음, 1개 이상)</h3>
            <input className={`${inputCls} h-7 max-w-[10rem] text-xs`} placeholder="검색" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <ul className="max-h-56 divide-y divide-line overflow-y-auto rounded-md border border-line">
            {filteredTools.map((t) => (
              <li key={t.name} className="flex items-center gap-2 px-2.5 py-1.5 text-xs">
                <input type="checkbox" className="accent-[var(--accent)]" checked={allowedTools.includes(t.name)}
                  onChange={() => toggleTool(t.name)} />
                <span className="truncate font-mono">{t.name}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="flex items-center justify-between gap-2">
          <button disabled={busy} onClick={save} className={btnPrimary}>
            <Save className={ICON} aria-hidden /> 저장
          </button>
          {agent && (
            <button disabled={busy} onClick={() => setConfirmDelete(true)} className={btnGhost}>
              <Trash2 className={ICON} aria-hidden /> 삭제
            </button>
          )}
        </div>
      </div>
      {confirmDelete && (
        <ConfirmDialog title={`'${agent?.name}' 에이전트를 삭제할까요?`} confirmLabel="삭제" danger busy={busy}
          onConfirm={remove} onCancel={() => setConfirmDelete(false)}>
          이 에이전트를 담당으로 쓰는 트리거가 있으면 삭제할 수 없습니다 — 먼저 그 트리거를 지우거나 바꾸세요.
        </ConfirmDialog>
      )}
    </Sheet>
  );
}

export function AgentsSection() {
  const [roles, setRoles] = useState<AgentRoleInfo[] | null>(null);
  const [profiles, setProfiles] = useState<AgentProfile[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [customAgents, setCustomAgents] = useState<CustomAgent[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [agentSheet, setAgentSheet] = useState<CustomAgent | "new" | null>(null);
  const load = useCallback(() => {
    api.get<AgentRoleInfo[]>("/api/admin/agents").then(setRoles).catch((e) => setError(e.message));
    api.get<AgentProfile[]>("/api/admin/agents/profiles").then(setProfiles).catch(() => {});
    api.get<SkillsResponse>("/api/admin/skills").then((d) => setSkills(d.skills)).catch(() => {});
    api.get<CustomAgent[]>("/api/admin/custom-agents").then(setCustomAgents).catch(() => {});
    api.get<ToolCatalog>("/api/admin/tools").then((c) => setTools(c.tools)).catch(() => {});
  }, []);
  useEffect(load, [load]);

  if (error && !roles) return <Banner>{error}</Banner>;
  if (!roles) return <SkeletonRows rows={4} />;
  const selectedRole = roles.find((r) => r.role === selected) ?? null;
  return (
    <div className="space-y-6">
      <div>
        <p className="mb-2 text-xs text-faint">
          고정된 5개 역할 — 카드를 눌러 프롬프트·이름 있는 프로파일·가시성을 관리합니다(CLAUDE.md §6 이 역할
          단위 제안/검증을 전제해 역할 수는 바꾸지 않음).
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {roles.map((r) => (
            <AgentCard key={r.role} role={r.role} prompts={r.prompts}
              profileCount={profiles.filter((p) => p.role === r.role).length}
              skillCount={skills.filter((s) => s.roles.includes(r.role)).length}
              onOpen={() => setSelected(r.role)} />
          ))}
        </div>
      </div>
      <div>
        <p className="mb-2 text-xs text-faint">
          커스텀 에이전트(레퍼런스 프로젝트 의 자유 에이전트 생성 재현) — 이름·프롬프트·모델·허용 도구를 자유롭게 정의해
          무제한 만들 수 있습니다. 자기 실행 경로는 없고, 진단 상세의 "트리거" 탭에서 트리거의 담당으로 고르면
          그 트리거가 발동할 때만 이 정의로 LLM 이 tool/args 를 고릅니다(실행은 여전히 policy.decide() 가 판정).
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {customAgents.map((a) => (
            <CustomAgentCard key={a.id} agent={a} onOpen={() => setAgentSheet(a)} />
          ))}
          <NewCustomAgentCard onOpen={() => setAgentSheet("new")} />
        </div>
      </div>
      {selectedRole && (
        <AgentDetailSheet
          key={selectedRole.role}
          role={selectedRole.role}
          prompts={selectedRole.prompts}
          profiles={profiles.filter((p) => p.role === selectedRole.role)}
          skillCount={skills.filter((s) => s.roles.includes(selectedRole.role)).length}
          onClose={() => setSelected(null)}
          onChanged={load}
        />
      )}
      {agentSheet && (
        <CustomAgentSheet
          key={agentSheet === "new" ? "new" : agentSheet.id}
          agent={agentSheet === "new" ? null : agentSheet}
          tools={tools}
          onClose={() => setAgentSheet(null)}
          onChanged={load}
        />
      )}
    </div>
  );
}
