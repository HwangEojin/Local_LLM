"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Bot, ClipboardList, Cpu, Crosshair, FileText, Lock, OctagonAlert, Plug, Plus, Power, PowerOff, RotateCcw, Save, ShieldCheck, SlidersHorizontal, Trash2, Wrench } from "lucide-react";

import { AuthGate } from "@/components/AuthGate";
import { Header } from "@/components/Header";
import { Banner, btnDanger, btnGhost, btnPrimary, btnSecondary, Card, ConfirmDialog, inputCls, SkeletonRows, useToast } from "@/components/ui";
import {
  api,
  ApiError,
  ChecklistEntry,
  KillSwitch,
  LlmProfileRef,
  LlmSettings,
  McpServer,
  Scope,
  Target,
  testLlm,
  WorkbenchSettings,
} from "@/lib/api";
import { formatTime } from "@/lib/labels";
import { AgentsSection } from "./AgentsSection";
import { ApprovalsSection } from "./ApprovalsSection";
import { errMsg, Field, ICON, NumField, ReadOnlyRow } from "./parts";
import { ToolsSection } from "./ToolsSection";

type SectionKey = "scope" | "llm" | "agents" | "mcp" | "tools" | "checklists" | "approvals" | "limits" | "kill-switch";
// href 가 있는 항목은 구역이 아니라 다른 화면으로 가는 링크다(감사 로그).
const SECTIONS: { key: SectionKey | "logs"; label: string; Icon: typeof Crosshair; href?: string }[] = [
  { key: "scope", label: "진단 대상", Icon: Crosshair },
  { key: "llm", label: "LLM", Icon: Cpu },
  { key: "agents", label: "에이전트", Icon: Bot },
  { key: "mcp", label: "MCP", Icon: Plug },
  { key: "tools", label: "도구", Icon: Wrench },
  { key: "checklists", label: "체크리스트", Icon: ClipboardList },
  { key: "approvals", label: "승인", Icon: ShieldCheck },
  { key: "logs", label: "로그", Icon: FileText, href: "/audit" },
  { key: "limits", label: "시스템", Icon: SlidersHorizontal },
  { key: "kill-switch", label: "중지 스위치", Icon: OctagonAlert },
];

const RISKS: { key: string; label: string; hint: string }[] = [
  { key: "read", label: "읽기", hint: "조회만 하는 도구" },
  { key: "write", label: "쓰기", hint: "대상 상태를 바꿀 수 있는 도구(승인 정책 적용)" },
  { key: "dangerous", label: "위험", hint: "서비스 장애·데이터 손상 가능성이 있는 도구" },
];

const emptyTarget = (): Target => ({ target_id: "", type: "url", value: "", platform: "", description: "", code: "" });

// ── 진단 대상 ──
function ScopeSection() {
  const toast = useToast();
  const [saved, setSaved] = useState<Scope | null>(null);
  const [draft, setDraft] = useState<Scope | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .get<Scope>("/api/admin/scope")
      .then((s) => {
        setSaved(s);
        setDraft(s);
      })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const dirty = useMemo(() => JSON.stringify(saved) !== JSON.stringify(draft), [saved, draft]);
  if (error && !draft) return <Banner>{error}</Banner>;
  if (!draft) return <SkeletonRows rows={4} />;

  const set = (patch: Partial<Scope>) => setDraft({ ...draft, ...patch });
  const setTarget = (i: number, patch: Partial<Target>) => set({ targets: draft.targets.map((t, j) => (j === i ? { ...t, ...patch } : t)) });
  const newDangerous = draft.allowed_risks.includes("dangerous") && !saved?.allowed_risks.includes("dangerous");

  // 빈 선택 필드는 키 자체를 빼서 서버 스키마(문자열 최소 길이·패턴)에 걸리지 않게 한다.
  const clean = (): Scope => ({
    ...draft,
    targets: draft.targets.map((t) => {
      const o: Target = { target_id: t.target_id.trim(), type: t.type, value: t.value.trim() };
      if (t.platform?.trim()) o.platform = t.platform.trim();
      if (t.description?.trim()) o.description = t.description.trim();
      if (t.code?.trim()) o.code = t.code.trim().toUpperCase();
      return o;
    }),
  });

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const body = clean();
      const res = await api.put<Scope>("/api/admin/scope", { scope: body, confirm: true, confirm_dangerous: body.allowed_risks.includes("dangerous") });
      setSaved(res);
      setDraft(res);
      toast("ok", "진단 대상을 저장했습니다");
    } catch (e) {
      setError(errMsg(e, "저장에 실패했습니다"));
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <div className="space-y-4">
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <Card className="space-y-4 p-4">
        <h2 className="text-sm font-semibold">진단 승인 정보</h2>
        <div className="grid gap-4 md:grid-cols-3">
          <Field label="계약/진단 ID (engagement)">
            <input className={inputCls} value={draft.engagement_id} onChange={(e) => set({ engagement_id: e.target.value })} />
          </Field>
          <Field label="승인자">
            <input className={inputCls} value={draft.approved_by} onChange={(e) => set({ approved_by: e.target.value })} />
          </Field>
          <Field label="유효기간 (이 날짜까지)" hint="지나면 새 진단을 만들 수 없고 실행 중인 진단도 멈춥니다">
            <input type="date" className={inputCls} value={draft.valid_until} onChange={(e) => set({ valid_until: e.target.value })} />
          </Field>
        </div>
        <fieldset>
          <legend className="mb-1.5 text-sm font-medium text-muted">허용 위험도</legend>
          <div className="flex flex-wrap gap-2">
            {RISKS.map((r) => {
              const on = draft.allowed_risks.includes(r.key);
              return (
                <label key={r.key} className={`flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-sm ${on ? "border-accent bg-accent/10" : "border-line hover:bg-raised"}`}>
                  <input
                    type="checkbox"
                    className="mt-1 accent-[var(--accent)]"
                    checked={on}
                    onChange={(e) => set({ allowed_risks: e.target.checked ? [...draft.allowed_risks, r.key] : draft.allowed_risks.filter((x) => x !== r.key) })}
                  />
                  <span>
                    {r.label}
                    <span className="block text-xs text-faint">{r.hint}</span>
                  </span>
                </label>
              );
            })}
          </div>
        </fieldset>
      </Card>

      <Card className="p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">
            진단 대상 <span className="font-normal text-faint">({draft.targets.length})</span>
          </h2>
          <button onClick={() => set({ targets: [...draft.targets, emptyTarget()] })} className={btnSecondary}>
            <Plus className={ICON} aria-hidden /> 대상 추가
          </button>
        </div>
        <div className="space-y-3">
          {draft.targets.map((t, i) => (
            <div key={i} className="rounded-md border border-line bg-canvas p-3">
              <div className="grid gap-3 md:grid-cols-12">
                <div className="md:col-span-3">
                  <Field label="대상 ID">
                    <input className={inputCls} value={t.target_id} onChange={(e) => setTarget(i, { target_id: e.target.value })} placeholder="baseline-demo" />
                  </Field>
                </div>
                <div className="md:col-span-2">
                  <Field label="종류">
                    <select className={inputCls} value={t.type} onChange={(e) => setTarget(i, { type: e.target.value })}>
                      <option value="url">URL</option>
                      <option value="host">호스트</option>
                      <option value="path">경로</option>
                    </select>
                  </Field>
                </div>
                <div className="md:col-span-5">
                  <Field label="값">
                    <input className={inputCls} value={t.value} onChange={(e) => setTarget(i, { value: e.target.value })} placeholder="https://example.test 또는 localhost 또는 C:\path" />
                  </Field>
                </div>
                <div className="md:col-span-2">
                  <Field label="플랫폼" hint="체크리스트 대조용">
                    <input className={inputCls} value={t.platform ?? ""} onChange={(e) => setTarget(i, { platform: e.target.value })} placeholder="WEB" />
                  </Field>
                </div>
                <div className="md:col-span-3">
                  <Field label="진단 ID 약어" hint="2~6자 영문 대문자·숫자. 비우면 대상 ID에서 자동 생성">
                    <input className={inputCls} value={t.code ?? ""} onChange={(e) => setTarget(i, { code: e.target.value.toUpperCase() })} maxLength={6} placeholder="BASE" />
                  </Field>
                </div>
                <div className="md:col-span-8">
                  <Field label="설명">
                    <input className={inputCls} value={t.description ?? ""} onChange={(e) => setTarget(i, { description: e.target.value })} />
                  </Field>
                </div>
                <div className="flex items-end md:col-span-1">
                  <button
                    onClick={() => set({ targets: draft.targets.filter((_, j) => j !== i) })}
                    disabled={draft.targets.length <= 1}
                    className={btnGhost}
                    aria-label={`대상 ${t.target_id || i + 1} 삭제`}
                    title={draft.targets.length <= 1 ? "대상은 1개 이상 필요합니다" : "삭제"}
                  >
                    <Trash2 className={ICON} aria-hidden />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <div className="flex items-center gap-2">
        <button disabled={!dirty || busy} onClick={() => setConfirming(true)} className={btnPrimary}>
          <Save className={ICON} aria-hidden /> 저장
        </button>
        <button disabled={!dirty || busy} onClick={() => setDraft(saved)} className={btnSecondary}>
          <RotateCcw className={ICON} aria-hidden /> 되돌리기
        </button>
        {dirty && <span className="text-xs text-warn">저장하지 않은 변경이 있습니다</span>}
      </div>

      {confirming && (
        <ConfirmDialog title="진단 대상을 저장할까요?" confirmLabel="저장" danger={newDangerous} busy={busy} onConfirm={save} onCancel={() => setConfirming(false)}>
          <p>진단할 수 있는 범위를 바꾸는 중요한 설정입니다. 변경 전/후 값이 감사 로그에 기록됩니다.</p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs">
            <li>이미 만든 진단은 생성 시점의 범위 사본을 쓰지만, 삭제·변경된 대상은 실행 중에도 제외됩니다.</li>
            <li>저장 전 서버가 만료일·중복 ID·경로·URL 형식을 검증합니다.</li>
            {newDangerous && <li className="font-medium text-danger">‘위험’ 위험도를 새로 허용합니다. 서비스 장애나 데이터 손상 가능성이 있는 작업이 승인 대상에 포함됩니다.</li>}
          </ul>
        </ConfirmDialog>
      )}
    </div>
  );
}

// ── 제외·예산·제한 ──
function LimitsSection() {
  const toast = useToast();
  const [saved, setSaved] = useState<WorkbenchSettings | null>(null);
  const [draft, setDraft] = useState<WorkbenchSettings | null>(null);
  const [excludeText, setExcludeText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const apply = (w: WorkbenchSettings) => {
    setSaved(w);
    setDraft(w);
    setExcludeText(w.exclude.join("\n"));
  };
  useEffect(() => {
    api.get<WorkbenchSettings>("/api/admin/workbench").then(apply).catch((e) => setError(e.message));
  }, []);

  const excludeList = excludeText.split("\n").map((s) => s.trim()).filter(Boolean);
  const dirty = !!saved && !!draft && JSON.stringify(saved) !== JSON.stringify({ ...draft, exclude: excludeList });
  if (error && !draft) return <Banner>{error}</Banner>;
  if (!draft) return <SkeletonRows rows={4} />;

  const setBudget = (k: keyof WorkbenchSettings["budget"], v: number) => setDraft({ ...draft, budget: { ...draft.budget, [k]: v } });

  async function save() {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.put<WorkbenchSettings>("/api/admin/workbench", {
        exclude: excludeList,
        lease_s: draft.lease_s,
        max_attempts: draft.max_attempts,
        approval_ttl_h: draft.approval_ttl_h,
        min_call_interval_ms: draft.min_call_interval_ms,
        budget: draft.budget,
        confirm: true,
      });
      apply(res);
      toast("ok", "설정을 저장했습니다");
    } catch (e) {
      setError(errMsg(e, "저장에 실패했습니다"));
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <div className="space-y-4">
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <Card className="p-4">
        <h2 className="mb-1 text-sm font-semibold">제외 대상</h2>
        <p className="mb-2 text-xs text-faint">한 줄에 하나. 호스트 이름이나 경로 접두어와 일치하면 범위 안이어도 도구 호출을 차단합니다.</p>
        <textarea rows={5} className={`${inputCls} font-mono`} value={excludeText} onChange={(e) => setExcludeText(e.target.value)} placeholder={"admin.example.test\nC:/path/to/secrets"} />
      </Card>
      <Card className="space-y-4 p-4">
        <h2 className="text-sm font-semibold">새 진단의 기본 예산</h2>
        <div className="grid gap-4 md:grid-cols-3">
          <NumField label="최대 작업 수" min={1} value={draft.budget.max_tasks} onChange={(n) => setBudget("max_tasks", n)} />
          <NumField label="최대 도구 호출 수 (체크리스트)" min={1} value={draft.budget.max_tool_calls} onChange={(n) => setBudget("max_tool_calls", n)} />
          <NumField label="최대 실행 시간 (초)" min={1} value={draft.budget.timeout_s} onChange={(n) => setBudget("timeout_s", n)} />
          <NumField label="최대 탐색 횟수" value={draft.budget.max_explorations} onChange={(n) => setBudget("max_explorations", n)} />
          <NumField label="탐색 전용 도구 호출 수" min={1} value={draft.budget.max_tool_calls_exploration} onChange={(n) => setBudget("max_tool_calls_exploration", n)} />
        </div>
      </Card>
      <Card className="space-y-4 p-4">
        <h2 className="text-sm font-semibold">실행 제어</h2>
        <div className="grid gap-4 md:grid-cols-3">
          <NumField label="작업 점유 시간 (초)" min={1} value={draft.lease_s} onChange={(n) => setDraft({ ...draft, lease_s: n })} />
          <NumField label="작업 재시도 횟수" min={1} value={draft.max_attempts} onChange={(n) => setDraft({ ...draft, max_attempts: n })} />
          <NumField label="도구 호출 최소 간격 (ms)" hint="요청 속도 제한" value={draft.min_call_interval_ms} onChange={(n) => setDraft({ ...draft, min_call_interval_ms: n })} />
        </div>
        <div className="space-y-2">
          <ReadOnlyRow label="nmap 경로" value={draft.nmap_path ?? ""} />
          <ReadOnlyRow label="testssl 경로" value={draft.testssl_path ?? ""} />
          <p className="text-xs text-faint">실행 파일 경로는 임의 명령 실행으로 이어질 수 있어 웹에서 바꿀 수 없습니다. config/workbench.json 을 직접 수정하세요.</p>
        </div>
      </Card>
      <div className="flex items-center gap-2">
        <button disabled={!dirty || busy} onClick={() => setConfirming(true)} className={btnPrimary}>
          <Save className={ICON} aria-hidden /> 저장
        </button>
        <button disabled={!dirty || busy} onClick={() => saved && apply(saved)} className={btnSecondary}>
          <RotateCcw className={ICON} aria-hidden /> 되돌리기
        </button>
      </div>
      {confirming && (
        <ConfirmDialog title="설정을 저장할까요?" confirmLabel="저장" busy={busy} onConfirm={save} onCancel={() => setConfirming(false)}>
          제외 목록과 예산은 이후 새로 만드는 진단과 다음 도구 호출 판정부터 적용됩니다. 변경 전/후 값이 감사 로그에 기록됩니다.
        </ConfirmDialog>
      )}
    </div>
  );
}

// ── LLM·호출 제한 ──
function LlmSection() {
  const toast = useToast();
  const [saved, setSaved] = useState<LlmSettings | null>(null);
  const [draft, setDraft] = useState<LlmSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  // 설치된 Ollama 모델(null = 연결 불가), 등록된 프로파일, 연결 테스트 결과(키: "model" 또는 프로파일 이름)
  const [models, setModels] = useState<string[] | null | undefined>(undefined);
  const [profiles, setProfiles] = useState<LlmProfileRef[]>([]);
  const [tests, setTests] = useState<Record<string, { busy: boolean; ok?: boolean; text?: string }>>({});
  const [refreshing, setRefreshing] = useState(false);

  const loadModels = useCallback(async () => {
    setRefreshing(true);
    try {
      const r = await api.get<{ models: string[] | null; profiles: LlmProfileRef[] }>("/api/environment/ollama-models");
      setModels(r.models);
      setProfiles(r.profiles ?? []);
    } catch {
      setModels(null);
    } finally {
      setRefreshing(false);
    }
  }, []);

  async function runTest(key: string, spec: string | null, provider = "ollama") {
    setTests((t) => ({ ...t, [key]: { busy: true } }));
    const r = await testLlm(spec, provider);
    setTests((t) => ({ ...t, [key]: { busy: false, ok: r.ok, text: r.text } }));
  }

  useEffect(() => {
    loadModels();
    api
      .get<LlmSettings>("/api/admin/settings")
      .then((s) => {
        setSaved(s);
        setDraft(s);
      })
      .catch((e) => setError(e.message));
  }, []);

  const dirty = useMemo(() => JSON.stringify(saved) !== JSON.stringify(draft), [saved, draft]);
  if (error && !draft) return <Banner>{error}</Banner>;
  if (!draft) return <SkeletonRows rows={4} />;

  const o = draft.ollama;
  const setO = (patch: Partial<LlmSettings["ollama"]>) => setDraft({ ...draft, ollama: { ...o, ...patch } });
  const setL = (patch: Partial<LlmSettings["limits"]>) => setDraft({ ...draft, limits: { ...draft.limits, ...patch } });

  async function save() {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.put<LlmSettings>("/api/admin/settings", { ollama: draft.ollama, limits: draft.limits, confirm: true });
      setSaved(res);
      setDraft(res);
      toast("ok", "설정을 저장했습니다");
    } catch (e) {
      setError(errMsg(e, "저장에 실패했습니다"));
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <div className="space-y-4">
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <Card className="space-y-4 p-4">
        <h2 className="text-sm font-semibold">로컬 LLM (Ollama)</h2>
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="주소" hint="로컬(127.0.0.1/localhost) 주소만 웹에서 지정할 수 있습니다">
            <input className={inputCls} value={o.base_url} onChange={(e) => setO({ base_url: e.target.value })} />
          </Field>
          <Field label="모델" hint={models === null ? "Ollama에 연결할 수 없어 직접 입력합니다" : "이 PC의 Ollama에 설치된 모델에서 고릅니다"}>
            {models ? (
              <select className={inputCls} value={o.model} onChange={(e) => setO({ model: e.target.value })}>
                {!models.includes(o.model) && <option value={o.model}>{o.model} (설치되지 않음)</option>}
                {models.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            ) : (
              <input className={inputCls} value={o.model} onChange={(e) => setO({ model: e.target.value })} />
            )}
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button type="button" className={btnSecondary} disabled={tests.model?.busy} onClick={() => runTest("model", `ollama:${o.model}`)}>
                연결 테스트
              </button>
              <button type="button" className={btnGhost} disabled={refreshing} onClick={loadModels}>
                {refreshing ? "불러오는 중…" : "설치된 모델 새로고침"}
              </button>
              {tests.model && (tests.model.busy
                ? <span className="text-xs text-muted">테스트 중…</span>
                : <span className={`text-xs ${tests.model.ok ? "text-ok" : "text-danger"}`}>{tests.model.text}</span>)}
            </div>
            {dirty && <p className="mt-1 text-xs text-faint">테스트는 저장된 주소로 연결합니다(주소를 바꿨다면 먼저 저장하세요).</p>}
          </Field>
          <NumField label="응답 대기 시간 (초)" min={1} value={o.timeout_s} onChange={(n) => setO({ timeout_s: n })} />
          <NumField label="진단당 최대 LLM 호출 수" min={1} value={o.max_calls_per_run} onChange={(n) => setO({ max_calls_per_run: n })} />
          <NumField label="최대 요청 크기 (바이트)" min={1} value={o.max_request_bytes} onChange={(n) => setO({ max_request_bytes: n })} />
          <NumField label="최대 응답 크기 (바이트)" min={1} value={o.max_response_bytes} onChange={(n) => setO({ max_response_bytes: n })} />
          <NumField label="재시도 횟수 (0~5)" value={o.max_retries} onChange={(n) => setO({ max_retries: n })} />
        </div>
        <ReadOnlyRow label="외부 LLM API 허용" value={draft.external_api.enabled ? "허용됨" : "차단됨"} />
        <p className="text-xs text-faint">외부 API 허용 여부는 데이터가 밖으로 나갈 수 있는 설정이라 웹에서 바꿀 수 없습니다. config/settings.json 을 직접 수정하세요.</p>
      </Card>
      <Card className="space-y-3 p-4">
        <h2 className="text-sm font-semibold">등록된 LLM 프로파일</h2>
        {profiles.length === 0 ? (
          <p className="text-xs text-faint">등록된 프로파일이 없습니다. config/llm_profiles.json 에 추가하면 새 진단의 에이전트별 모델로 고를 수 있습니다.</p>
        ) : (
          <ul className="divide-y divide-line text-sm">
            {profiles.map((p) => (
              <li key={p.name} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  <span className="font-medium">{p.name}</span>
                  <span className="ml-2 text-xs text-muted">{p.kind} · {p.model ?? "기본 모델"}</span>
                </span>
                <span className="flex items-center gap-2">
                  {tests[p.name] && (tests[p.name].busy
                    ? <span className="text-xs text-muted">테스트 중…</span>
                    : <span className={`text-xs ${tests[p.name].ok ? "text-ok" : "text-danger"}`}>{tests[p.name].text}</span>)}
                  <button type="button" className={btnSecondary} disabled={tests[p.name]?.busy} onClick={() => runTest(p.name, p.name, p.kind)}>
                    연결 테스트
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-faint">프로파일은 주소·모델을 바꿀 수 있어 웹에서 편집하지 않습니다. config/llm_profiles.json 을 직접 수정하세요.</p>
      </Card>
      <Card className="space-y-4 p-4">
        <h2 className="text-sm font-semibold">진단당 호출·증적 제한</h2>
        <div className="grid gap-4 md:grid-cols-3">
          <NumField label="최대 도구 호출 수" min={1} value={draft.limits.max_tool_calls_per_run} onChange={(n) => setL({ max_tool_calls_per_run: n })} />
          <NumField label="최대 실행 시간 (초)" min={1} value={draft.limits.run_timeout_s} onChange={(n) => setL({ run_timeout_s: n })} />
          <NumField label="증적 1건 최대 크기 (바이트)" min={1} value={draft.limits.max_evidence_bytes} onChange={(n) => setL({ max_evidence_bytes: n })} />
        </div>
      </Card>
      <div className="flex items-center gap-2">
        <button disabled={!dirty || busy} onClick={() => setConfirming(true)} className={btnPrimary}>
          <Save className={ICON} aria-hidden /> 저장
        </button>
        <button disabled={!dirty || busy} onClick={() => setDraft(saved)} className={btnSecondary}>
          <RotateCcw className={ICON} aria-hidden /> 되돌리기
        </button>
      </div>
      {confirming && (
        <ConfirmDialog title="LLM·제한 설정을 저장할까요?" confirmLabel="저장" busy={busy} onConfirm={save} onCancel={() => setConfirming(false)}>
          변경 전/후 값이 감사 로그에 기록됩니다. 저장 후 새로 시작하는 진단부터 적용됩니다.
        </ConfirmDialog>
      )}
    </div>
  );
}

// ── MCP·체크리스트 ──
function McpSection({ part }: { part: "mcp" | "checklists" }) {
  const toast = useToast();
  const [servers, setServers] = useState<McpServer[] | null>(null);
  const [checklists, setChecklists] = useState<ChecklistEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<McpServer | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<McpServer[]>("/api/admin/mcp").then(setServers).catch((e) => setError(e.message));
    api.get<ChecklistEntry[]>("/api/admin/checklists").then(setChecklists).catch(() => setChecklists([]));
  }, []);

  async function toggle(s: McpServer) {
    setBusy(true);
    try {
      setServers(await api.put<McpServer[]>(`/api/admin/mcp/${encodeURIComponent(s.name)}`, { enabled: !s.enabled, confirm: true }));
      toast("ok", `${s.name}: ${s.enabled ? "비활성화" : "활성화"}했습니다`);
    } catch (e) {
      toast("danger", errMsg(e, "변경에 실패했습니다"));
    } finally {
      setBusy(false);
      setTarget(null);
    }
  }

  return (
    <div className="space-y-4">
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      {part === "mcp" && <Card className="p-4">
        <h2 className="mb-3 text-sm font-semibold">MCP 서버</h2>
        {!servers ? (
          <SkeletonRows rows={3} />
        ) : (
          <ul className="divide-y divide-line">
            {servers.map((s) => (
              <li key={s.name} className="flex items-start justify-between gap-4 py-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm font-medium">{s.name}</span>
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${s.enabled ? "bg-ok/15 text-ok" : "bg-raised text-muted"}`}>{s.enabled ? "활성" : "비활성"}</span>
                    <span className="text-xs text-faint">{s.transport} · 제한시간 {s.timeout_s}초 · 도구 {s.tools.length}개{s.source === "local" ? " · 이 PC 설정" : ""}</span>
                  </div>
                  {s.description && <p className="mt-1 line-clamp-2 text-xs text-muted">{s.description}</p>}
                </div>
                <button disabled={busy} onClick={() => (s.enabled ? toggle(s) : setTarget(s))} className={`${s.enabled ? btnSecondary : btnPrimary} shrink-0 whitespace-nowrap`}>
                  {s.enabled ? <PowerOff className={ICON} aria-hidden /> : <Power className={ICON} aria-hidden />}
                  {s.enabled ? "비활성화" : "활성화"}
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-xs text-faint">명령·주소·허용 도구는 웹에서 바꿀 수 없고 활성/비활성만 바꿉니다. 변경은 이 PC 전용 파일(mcp_servers.local.json)에 저장됩니다.</p>
      </Card>}

      {part === "checklists" && <Card className="p-4">
        <h2 className="mb-3 text-sm font-semibold">체크리스트</h2>
        {!checklists ? (
          <SkeletonRows rows={2} />
        ) : checklists.length === 0 ? (
          <p className="text-sm text-faint">등록된 체크리스트가 없습니다. 자율 탐색만 사용할 수 있습니다.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-muted">
              <tr>
                <th scope="col" className="py-1 font-medium">ID</th>
                <th scope="col" className="py-1 font-medium">버전</th>
                <th scope="col" className="py-1 font-medium">상태</th>
                <th scope="col" className="py-1 font-medium">SHA-256</th>
              </tr>
            </thead>
            <tbody>
              {checklists.map((c) => (
                <tr key={`${c.checklist_id}@${c.version}`} className="border-t border-line">
                  <td className="py-1.5 font-mono text-xs">{c.checklist_id}</td>
                  <td className="py-1.5">{c.version}</td>
                  <td className="py-1.5">{c.status === "active" ? "활성" : c.status}</td>
                  <td className="py-1.5 font-mono text-xs text-faint">{c.sha256?.slice(0, 12) ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="mt-3 text-xs text-faint">체크리스트 활성화는 원본 해시 검증과 사람의 확인이 필요해 현재는 CLI(checklist activate)에서만 합니다. 여기서는 조회만 가능합니다.</p>
      </Card>}

      {target && (
        <ConfirmDialog title={`${target.name} 서버를 활성화할까요?`} confirmLabel="활성화" busy={busy} onConfirm={() => toggle(target)} onCancel={() => setTarget(null)}>
          활성화하면 이 서버의 도구 {target.tools.length}개를 진단에서 호출할 수 있게 됩니다. 호출은 여전히 범위·정책 검사를 통과해야 합니다.
        </ConfirmDialog>
      )}
    </div>
  );
}

// ── 중지 스위치 ──
function KillSwitchSection() {
  const toast = useToast();
  const [ks, setKs] = useState<KillSwitch | null>(null);
  const [mode, setMode] = useState<"engage" | "release" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get<KillSwitch>("/api/admin/kill-switch").then(setKs).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function submit(reason: string) {
    if (!mode) return;
    setBusy(true);
    try {
      setKs(await api.post<KillSwitch>(`/api/admin/kill-switch/${mode}`, { reason, confirm: true }));
      window.dispatchEvent(new Event("wb-killswitch")); // 상단 경고 띠 즉시 갱신
      toast("ok", mode === "engage" ? "중지 스위치를 켰습니다" : "중지 스위치를 해제했습니다");
    } catch (e) {
      setError(errMsg(e, "변경에 실패했습니다"));
    } finally {
      setBusy(false);
      setMode(null);
    }
  }

  if (error && !ks) return <Banner>{error}</Banner>;
  if (!ks) return <SkeletonRows rows={2} />;

  return (
    <div className="space-y-4">
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <Card className={`p-5 ${ks.active ? "border-danger/50" : ""}`}>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-base font-semibold">
              <OctagonAlert className={`h-5 w-5 ${ks.active ? "text-danger" : "text-faint"}`} aria-hidden />
              {ks.active ? "켜짐 — 모든 도구 호출 차단 중" : "꺼짐 — 정상 동작"}
            </div>
            {ks.active && (
              <p className="mt-1 text-sm text-muted">
                {ks.reason && <>사유: {ks.reason} · </>}
                {ks.by && <>{ks.by} · </>}
                {ks.at && formatTime(ks.at)}
              </p>
            )}
          </div>
          {ks.active ? (
            <button onClick={() => setMode("release")} className={btnSecondary}>
              <Power className={ICON} aria-hidden /> 해제 (재승인)
            </button>
          ) : (
            <button onClick={() => setMode("engage")} className={btnDanger}>
              <OctagonAlert className={ICON} aria-hidden /> 중지 스위치 켜기
            </button>
          )}
        </div>
        <ul className="mt-4 list-disc space-y-1 pl-5 text-xs text-faint">
          <li>켜는 즉시 신규 도구 호출과 후속 작업 생성이 멈추고, 진행 중인 MCP 호출도 취소를 시도합니다.</li>
          <li>이미 대상에 전달된 요청의 부작용은 취소되지 않습니다.</li>
          <li>해제해도 멈춘 진단은 자동으로 재개되지 않습니다. 진단 상세에서 직접 ‘재개’해야 합니다.</li>
        </ul>
      </Card>

      {mode === "engage" && (
        <ConfirmDialog title="중지 스위치를 켤까요?" confirmLabel="켜기" danger withReason busy={busy} onConfirm={submit} onCancel={() => setMode(null)}>
          실행 중인 모든 진단이 새 도구 호출을 하지 못하게 됩니다. 사고나 의도하지 않은 동작이 의심될 때 사용하세요.
        </ConfirmDialog>
      )}
      {mode === "release" && (
        <ConfirmDialog title="중지 스위치를 해제할까요?" confirmLabel="해제" withReason busy={busy} onConfirm={submit} onCancel={() => setMode(null)}>
          원인을 확인하고 다시 진행해도 된다는 사람의 재승인입니다. 사유가 감사 로그에 기록됩니다.
        </ConfirmDialog>
      )}
    </div>
  );
}

function SettingsBody() {
  // AuthGate 통과 후에만 렌더되므로(클라이언트 전용) lazy 초기화에서 location 을 읽어도 안전하다.
  const [section, setSectionState] = useState<SectionKey>(() => {
    const h = window.location.hash.slice(1) as SectionKey;
    return SECTIONS.some((s) => s.key === h && !s.href) ? h : "scope";
  });
  const setSection = (k: SectionKey) => {
    setSectionState(k);
    window.history.replaceState(null, "", `#${k}`);
  };
  return (
    <main className="mx-auto w-full max-w-6xl p-6">
      <h1 className="mb-1 text-lg font-semibold">설정</h1>
      <p className="mb-5 text-sm text-muted">진단 환경을 제어합니다. 모든 변경은 감사 로그에 기록됩니다.</p>
      <div className="grid gap-6 md:grid-cols-[13rem_1fr]">
        <nav aria-label="설정 구역" className="flex gap-1 md:flex-col">
          {SECTIONS.map(({ key, label, Icon, href }) => href ? (
            <Link
              key={key}
              href={href}
              className="flex items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-muted transition-colors hover:bg-raised/60 hover:text-ink"
            >
              <Icon className={ICON} aria-hidden />
              {label}
            </Link>
          ) : (
            <button
              key={key}
              onClick={() => setSection(key as SectionKey)}
              aria-current={section === key ? "page" : undefined}
              className={`flex items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors ${section === key ? "bg-raised font-medium text-ink" : "text-muted hover:bg-raised/60 hover:text-ink"}`}
            >
              <Icon className={ICON} aria-hidden />
              {label}
            </button>
          ))}
        </nav>
        <div>
          {section === "scope" && <ScopeSection />}
          {section === "limits" && <LimitsSection />}
          {section === "llm" && <LlmSection />}
          {section === "agents" && <AgentsSection />}
          {section === "mcp" && <McpSection part="mcp" />}
          {section === "tools" && <ToolsSection />}
          {section === "checklists" && <McpSection part="checklists" />}
          {section === "approvals" && <ApprovalsSection />}
          {section === "kill-switch" && <KillSwitchSection />}
        </div>
      </div>
    </main>
  );
}

export default function SettingsPage() {
  return (
    <AuthGate>
      <Header />
      <SettingsBody />
    </AuthGate>
  );
}
