"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Play, Settings } from "lucide-react";

import { AuthGate } from "@/components/AuthGate";
import { Header } from "@/components/Header";
import { Banner, btnPrimary, btnSecondary, Card, inputCls, SkeletonRows } from "@/components/ui";
import { api, testLlm, ApiError, Environment, RunDetail, WorkbenchSettings } from "@/lib/api";
import { AGENT_ROLE_LABEL, AUTONOMY_LABEL, DOCTOR_LABEL } from "@/lib/labels";

const DOCTOR_TONE: Record<string, string> = {
  OK: "text-ok",
  WARN: "text-warn",
  FAIL: "text-danger",
  INFO: "text-faint",
};

function DoctorPanel({ doctor }: { doctor: Environment["doctor"] }) {
  const bad = doctor.filter((d) => d.status === "FAIL").length;
  const warn = doctor.filter((d) => d.status === "WARN").length;
  // 문제가 없으면 접어 두고, 주의/오류가 있으면 펼쳐서 눈에 띄게 한다.
  return (
    <details open={bad + warn > 0} className="mb-5 rounded-lg border border-line bg-surface text-sm">
      <summary className="cursor-pointer select-none px-4 py-3 font-medium">
        환경 점검{" "}
        <span className={bad ? "text-danger" : warn ? "text-warn" : "text-ok"}>
          {bad ? `오류 ${bad}건` : warn ? `주의 ${warn}건` : "이상 없음"}
        </span>
      </summary>
      <ul className="space-y-1 border-t border-line px-4 py-3">
        {doctor.map((d, i) => (
          <li key={i} className="flex gap-2">
            <span className={`w-10 shrink-0 font-medium ${DOCTOR_TONE[d.status] ?? "text-muted"}`}>
              {DOCTOR_LABEL[d.status] ?? d.status}
            </span>
            <span>
              <span className="text-ink">{d.name}</span> <span className="text-muted">{d.detail}</span>
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function TestChip({ t }: { t?: { busy: boolean; ok?: boolean; text?: string } }) {
  if (!t) return null;
  if (t.busy) return <span className="text-xs text-muted">테스트 중…</span>;
  return <span className={`text-xs ${t.ok ? "text-ok" : "text-danger"}`}>{t.text}</span>;
}

const labelCls = "mb-1.5 block text-sm font-medium text-muted";
const toNum = (s: string) => (s.trim() === "" ? undefined : Number(s));

function NewRunForm() {
  const router = useRouter();
  const [env, setEnv] = useState<Environment | null>(null);
  const [defaults, setDefaults] = useState<WorkbenchSettings["budget"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // 기존 진단을 복제해서 시작: /runs/new?from=<진단 ID>. 이 컴포넌트는 AuthGate 뒤(클라이언트 전용)라 lazy 초기화가 안전하다.
  const [from] = useState(() => new URLSearchParams(window.location.search).get("from"));
  const [cloneNote, setCloneNote] = useState<string | null>(null);

  const [targets, setTargets] = useState<string[]>([]);
  const [operator, setOperator] = useState("");
  const [checklist, setChecklist] = useState<string>("");
  const [useLlm, setUseLlm] = useState(true);
  const [autonomy, setAutonomy] = useState("approval");
  const [llmProvider, setLlmProvider] = useState("ollama");
  // 연결 테스트 결과. 키는 역할 이름 또는 "default"(실행 기본 모델)
  const [tests, setTests] = useState<Record<string, { busy: boolean; ok?: boolean; text?: string }>>({});
  const [refreshing, setRefreshing] = useState(false);
  const [agentModels, setAgentModels] = useState<Record<string, string>>({});
  const [goal, setGoal] = useState("");
  const [tools, setTools] = useState<string[]>([]);
  const [maxTasks, setMaxTasks] = useState("");
  const [maxExplorations, setMaxExplorations] = useState("");

  useEffect(() => {
    api.get<Environment>("/api/environment").then(setEnv).catch((e) => setError(e.message));
    api.get<WorkbenchSettings>("/api/admin/workbench").then((w) => setDefaults(w.budget)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!from) return;
    api
      .get<RunDetail>(`/api/runs/${encodeURIComponent(from)}`)
      .then((d) => {
        const r = d.run;
        setTargets(d.targets);
        setUseLlm(!!r.use_llm);
        setAutonomy(r.autonomy as string);
        setLlmProvider(r.llm_provider as string);
        setAgentModels((r.llm_agent_models ?? {}) as Record<string, string>);
        if (Array.isArray(r.allowed_tools)) setTools(r.allowed_tools as string[]);
        const b = (r.budget ?? {}) as Record<string, number>;
        if (b.max_tasks) setMaxTasks(String(b.max_tasks));
        if (b.max_explorations != null) setMaxExplorations(String(b.max_explorations));
        setCloneNote(`${r.run_code} 의 설정을 불러왔습니다. 필요한 부분만 바꿔서 시작하세요.`);
      })
      .catch((e) => setError(`복제할 진단을 불러오지 못했습니다: ${e.message}`));
  }, [from]);

  async function testModel(key: string, spec: string | null) {
    setTests((t) => ({ ...t, [key]: { busy: true } }));
    const r = await testLlm(spec, llmProvider);
    setTests((t) => ({ ...t, [key]: { busy: false, ok: r.ok, text: r.text } }));
  }

  async function refreshModels() {
    setRefreshing(true);
    try {
      const r = await api.get<{ models: string[] | null }>("/api/environment/ollama-models");
      setEnv((e) => (e ? { ...e, ollama_models: r.models } : e));
    } finally {
      setRefreshing(false);
    }
  }

  function toggle(list: string[], set: (v: string[]) => void, id: string) {
    set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (targets.length === 0) {
      setError("대상을 하나 이상 선택하세요");
      return;
    }
    if (!checklist && !useLlm) {
      setError("체크리스트 없이 실행하려면 ‘LLM 자율 탐색 사용’이 필요합니다");
      return;
    }
    setLoading(true);
    try {
      const run = await api.post<{ id: string; run_code: string }>("/api/runs", {
        targets,
        operator: operator || "operator",
        checklist: checklist || null,
        use_llm: useLlm,
        autonomy,
        llm_provider: llmProvider,
        agent_models: useLlm && Object.keys(agentModels).length ? agentModels : null,
        goal: goal.trim() || null,
        tools: tools.length ? tools : null,
        max_tasks: toNum(maxTasks),
        max_explorations: toNum(maxExplorations),
      });
      router.push(`/runs/${run.run_code}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "진단 생성에 실패했습니다");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="mx-auto w-full max-w-2xl p-6">
      <h1 className="mb-5 text-lg font-semibold">새 진단 만들기</h1>
      {cloneNote && <Banner tone="info">{cloneNote}</Banner>}
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      {!env ? (
        <SkeletonRows rows={6} />
      ) : (
        <>
          <DoctorPanel doctor={env.doctor} />
          <form onSubmit={onSubmit}>
            <Card className="space-y-6 p-6">
              <fieldset>
                <legend className={labelCls}>진단 대상</legend>
                {env.targets.length === 0 && (
                  <p className="text-sm text-faint">
                    등록된 대상이 없습니다.{" "}
                    <Link href="/settings#scope" className="text-accent hover:underline">
                      설정에서 대상을 추가하세요
                    </Link>
                  </p>
                )}
                <div className="space-y-1.5">
                  {env.targets.map((t) => (
                    <label
                      key={t.target_id}
                      className={`flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2 text-sm transition-colors ${
                        targets.includes(t.target_id) ? "border-accent bg-accent/10" : "border-line hover:bg-raised"
                      }`}
                    >
                      <input
                        type="checkbox"
                        className="mt-1 accent-[var(--accent)]"
                        checked={targets.includes(t.target_id)}
                        onChange={() => toggle(targets, setTargets, t.target_id)}
                      />
                      <span>
                        <span className="font-mono">{t.target_id}</span>{" "}
                        <span className="text-muted">
                          ({t.type}:{t.value}
                          {t.platform ? `, ${t.platform}` : ""})
                        </span>
                        {t.description && <div className="text-xs text-faint">{t.description}</div>}
                      </span>
                    </label>
                  ))}
                </div>
                <p className="mt-2 flex items-center gap-1.5 text-xs text-faint">
                  <Settings className="h-3.5 w-3.5" aria-hidden />
                  진단 ID는 첫 번째로 선택한 대상의 약어와 오늘 날짜, 순번으로 자동 부여됩니다. 예: BASE-20261023-01
                </p>
              </fieldset>

              <div>
                <label htmlFor="operator" className={labelCls}>
                  운영자
                </label>
                <input id="operator" className={inputCls} value={operator} onChange={(e) => setOperator(e.target.value)} placeholder="이름 (비우면 ‘operator’)" />
              </div>

              <div>
                <label htmlFor="goal" className={labelCls}>
                  진단 목표 (선택)
                </label>
                <textarea
                  id="goal"
                  rows={2}
                  className={inputCls}
                  value={goal}
                  onChange={(e) => setGoal(e.target.value)}
                  placeholder="비우면 ‘자율 진단: 대상 이름’으로 기록됩니다. 예: 로그인과 파일 업로드 기능 위주로 점검"
                />
              </div>

              <div>
                <label htmlFor="checklist" className={labelCls}>
                  체크리스트 (선택)
                </label>
                <select id="checklist" className={inputCls} value={checklist} onChange={(e) => setChecklist(e.target.value)}>
                  <option value="">없음 — 자율 탐색만</option>
                  {env.checklists.map((c) => (
                    <option key={`${c.checklist_id}@${c.version}`} value={`${c.checklist_id}@${c.version}`}>
                      {c.checklist_id}@{c.version}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" className="accent-[var(--accent)]" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
                  LLM 자율 탐색 사용
                </label>
                <p className="ml-6 mt-0.5 text-xs text-faint">체크리스트 없이 실행하려면 반드시 켜야 합니다.</p>
              </div>

              {useLlm && (
                <fieldset>
                  <legend className={labelCls}>LLM 제공자</legend>
                  <div className="space-y-1.5 text-sm">
                    {env.llm_providers.map((p) => (
                      <label key={p} className="flex items-start gap-2">
                        <input
                          type="radio"
                          name="llm_provider"
                          className="mt-1 accent-[var(--accent)]"
                          value={p}
                          checked={llmProvider === p}
                          onChange={() => setLlmProvider(p)}
                        />
                        <span>
                          {p === "ollama" ? "Ollama (기본, 로컬)" : "claude_cli (임시 테스트)"}
                          {p !== "ollama" && (
                            <span className="block text-xs text-faint">이 PC에 로그인된 claude CLI 구독을 호출합니다. API 과금은 아닙니다.</span>
                          )}
                        </span>
                      </label>
                    ))}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-3">
                    <button type="button" className={btnSecondary} disabled={tests.default?.busy} onClick={() => testModel("default", null)}>
                      기본 모델 연결 테스트
                    </button>
                    <TestChip t={tests.default} />
                  </div>
                </fieldset>
              )}

              {useLlm && (
                <fieldset>
                  <legend className={labelCls}>에이전트별 모델 (선택)</legend>
                  <p className="mb-2 text-xs text-faint">
                    비워 두면 위에서 고른 실행 기본 모델을 씁니다. 목록은 이 PC의 Ollama에 설치된 모델과 등록된 프로파일입니다.
                    {env.ollama_models === null && <span className="text-warn"> Ollama에 연결할 수 없어 설치된 모델을 확인하지 못했습니다.</span>}
                    <button type="button" className="ml-2 text-accent hover:underline" disabled={refreshing} onClick={refreshModels}>
                      {refreshing ? "불러오는 중…" : "설치된 모델 새로고침"}
                    </button>
                  </p>
                  <div className="space-y-1.5 text-sm">
                    {(env.agent_roles ?? []).map((role) => (
                      <div key={role}>
                        <div className="flex items-center justify-between gap-3">
                          <label htmlFor={`am-${role}`}>
                            {AGENT_ROLE_LABEL[role] ?? role}
                            <span className="ml-1 font-mono text-xs text-faint">{role}</span>
                          </label>
                          <div className="flex items-center gap-2">
                            <select
                              id={`am-${role}`}
                              className={`${inputCls} min-w-0 max-w-[14rem]`}
                              value={agentModels[role] ?? ""}
                              onChange={(e) => {
                                const { [role]: _drop, ...rest } = agentModels;
                                setAgentModels(e.target.value ? { ...rest, [role]: e.target.value } : rest);
                                setTests((t) => ({ ...t, [role]: { busy: false } }));
                              }}
                            >
                              <option value="">실행 기본 모델</option>
                              {(env.ollama_models ?? []).map((m) => (
                                <option key={m} value={`ollama:${m}`}>Ollama · {m}</option>
                              ))}
                              {(env.llm_profiles ?? []).map((p) => (
                                <option key={p.name} value={p.name}>프로파일 · {p.name}</option>
                              ))}
                            </select>
                            <button
                              type="button"
                              className={`${btnSecondary} shrink-0 whitespace-nowrap`}
                              disabled={tests[role]?.busy}
                              onClick={() => testModel(role, agentModels[role] ?? null)}
                            >
                              연결 테스트
                            </button>
                          </div>
                        </div>
                        <div className="mt-0.5 text-right"><TestChip t={tests[role]} /></div>
                      </div>
                    ))}
                  </div>
                </fieldset>
              )}

              <fieldset>
                <legend className={labelCls}>자율성</legend>
                <div className="space-y-1.5 text-sm">
                  {env.autonomy_levels.map((a) => (
                    <label key={a} className="flex items-start gap-2">
                      <input
                        type="radio"
                        name="autonomy"
                        className="mt-1 accent-[var(--accent)]"
                        value={a}
                        checked={autonomy === a}
                        onChange={() => setAutonomy(a)}
                      />
                      <span>
                        {AUTONOMY_LABEL[a] ?? a}
                        <span className="block text-xs text-faint">
                          {a === "approval" ? "위험 작업은 사람이 승인해야 실행됩니다." : "허용 범위 안의 작업은 승인 없이 자동 실행됩니다."}
                        </span>
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <details className="rounded-md border border-line">
                <summary className="cursor-pointer select-none px-3 py-2 text-sm font-medium text-muted">
                  예산·허용 도구 (선택) <span className="font-normal text-faint">{tools.length ? `· 도구 ${tools.length}개로 제한` : ""}</span>
                </summary>
                <div className="space-y-5 border-t border-line p-3">
                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <label htmlFor="max-tasks" className={labelCls}>
                        최대 작업 수
                      </label>
                      <input id="max-tasks" type="number" min={1} className={inputCls} value={maxTasks} onChange={(e) => setMaxTasks(e.target.value)} placeholder={defaults ? `기본 ${defaults.max_tasks}` : "기본값"} />
                    </div>
                    <div>
                      <label htmlFor="max-expl" className={labelCls}>
                        최대 탐색 횟수
                      </label>
                      <input id="max-expl" type="number" min={0} className={inputCls} value={maxExplorations} onChange={(e) => setMaxExplorations(e.target.value)} placeholder={defaults ? `기본 ${defaults.max_explorations}` : "기본값"} />
                    </div>
                  </div>
                  <fieldset>
                    <legend className={labelCls}>허용 도구 — 선택하지 않으면 등록된 모든 도구</legend>
                    <div className="grid max-h-56 gap-1 overflow-y-auto pr-1 sm:grid-cols-2">
                      {env.tools.map((t) => (
                        <label key={t.name} className="flex items-center gap-2 text-xs">
                          <input type="checkbox" className="accent-[var(--accent)]" checked={tools.includes(t.name)} onChange={() => toggle(tools, setTools, t.name)} />
                          <span className="truncate font-mono">{t.name}</span>
                          <span className="text-faint">{t.risk}</span>
                        </label>
                      ))}
                    </div>
                  </fieldset>
                  <p className="text-xs text-faint">기본 예산과 제한은 설정 화면에서 바꿀 수 있습니다.</p>
                </div>
              </details>

              <button type="submit" disabled={loading} className={`${btnPrimary} w-full py-2`}>
                <Play className="h-4 w-4" aria-hidden />
                {loading ? "생성 중…" : "진단 생성"}
              </button>
            </Card>
          </form>
        </>
      )}
    </main>
  );
}

export default function NewRunPage() {
  return (
    <AuthGate>
      <Header />
      <NewRunForm />
    </AuthGate>
  );
}
