// 스킬 구역: 역할 프롬프트 뒤에 덧붙는 운영자 지침 묶음. 스킬은 LLM 이 일하는 방식을 다듬는 문구일 뿐이라 도구·권한을
// 늘리지 못하고(정책 검사는 서버가 매 호출 독립적으로 한다) 위의 규칙을 덮어쓰지 못한다고 프롬프트에도 명시돼 있다.
import { useCallback, useEffect, useState } from "react";
import { FileText, FileUp, Plus, Save, Trash2 } from "lucide-react";

import { Banner, btnDanger, btnPrimary, btnSecondary, Card, ConfirmDialog, inputCls, Sheet, SkeletonRows, useToast } from "@/components/ui";
import { api, Skill, SkillsResponse, SkillUsage } from "@/lib/api";
import { AGENT_ROLE_LABEL, formatTime } from "@/lib/labels";
import { errMsg, Field, ICON } from "./parts";

const SKILL_MD_EXAMPLE = `---
name: evidence-first
description: 증거를 먼저 확보하고 나서 설명한다
license: MIT
roles:
  - worker
---
항상 실제 증거(evidence id)를 먼저 인용한 뒤에 설명할 것. 추측은 "추정"이라고 밝힐 것.`;

type Draft = { name: string; description: string; instructions: string; roles: string[]; enabled: boolean };
const NAME_RE = /^[A-Za-z0-9_.:-]{1,80}$/;
const blank = (): Draft => ({ name: "", description: "", instructions: "", roles: [], enabled: true });
const fromSkill = (s: Skill): Draft => ({ name: s.name, description: s.description, instructions: s.instructions, roles: s.roles, enabled: s.enabled });

function SkillEditor({ draft, setDraft, roles, isNew }: { draft: Draft; setDraft: (d: Draft) => void; roles: string[]; isNew: boolean }) {
  const toggleRole = (r: string) => setDraft({ ...draft, roles: draft.roles.includes(r) ? draft.roles.filter((x) => x !== r) : [...draft.roles, r] });
  return (
    <div className="space-y-3">
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="이름" hint={isNew ? "영문·숫자·._:- (최대 80자), 만든 뒤에는 바꿀 수 없습니다" : undefined}>
          <input className={inputCls} value={draft.name} disabled={!isNew} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="evidence-first" />
        </Field>
        <Field label="설명 (선택)">
          <input className={inputCls} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} maxLength={300} />
        </Field>
      </div>
      <Field label="지침" hint={`${draft.instructions.length} / 4000자`}>
        <textarea rows={6} className={`${inputCls} text-sm`} value={draft.instructions} maxLength={4000} onChange={(e) => setDraft({ ...draft, instructions: e.target.value })} />
      </Field>
      <fieldset>
        <legend className="mb-1 text-sm font-medium text-muted">붙일 역할</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {roles.map((r) => (
            <label key={r} className="flex items-center gap-1.5">
              <input type="checkbox" className="accent-[var(--accent)]" checked={draft.roles.includes(r)} onChange={() => toggleRole(r)} />
              {AGENT_ROLE_LABEL[r] ?? r}
            </label>
          ))}
        </div>
      </fieldset>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" className="accent-[var(--accent)]" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />
        사용
      </label>
    </div>
  );
}

function UsagePanel({ name }: { name: string }) {
  const [usage, setUsage] = useState<SkillUsage | null>(null);
  useEffect(() => {
    api.get<SkillUsage>(`/api/admin/skills/${encodeURIComponent(name)}/usage`).then(setUsage).catch(() => setUsage({ recent: [], by_role: [] }));
  }, [name]);
  if (!usage) return <p className="text-xs text-faint">불러오는 중…</p>;
  if (usage.recent.length === 0) return <p className="text-xs text-faint">아직 어떤 진단에도 쓰이지 않았습니다.</p>;
  return (
    <div className="space-y-2 text-xs">
      <div className="flex flex-wrap gap-3 text-muted">
        {usage.by_role.map((r) => (
          <span key={r.role}>{AGENT_ROLE_LABEL[r.role] ?? r.role}: 진단 {r.runs}건 · 호출 {r.calls}회</span>
        ))}
      </div>
      <table className="w-full">
        <thead className="text-left text-muted">
          <tr>
            <th className="py-1 font-medium">진단</th>
            <th className="py-1 font-medium">역할</th>
            <th className="py-1 font-medium">호출</th>
            <th className="py-1 font-medium">최근 사용</th>
          </tr>
        </thead>
        <tbody>
          {usage.recent.map((u) => (
            <tr key={`${u.run_id}-${u.role}`} className="border-t border-line">
              <td className="py-1 font-mono">{u.run_code ?? "(삭제된 진단)"}</td>
              <td className="py-1">{AGENT_ROLE_LABEL[u.role] ?? u.role}</td>
              <td className="py-1 tabular">{u.calls}</td>
              <td className="py-1">{formatTime(u.last_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ImportSkillCard({ roles, onImported }: { roles: string[]; onImported: (data: SkillsResponse) => void }) {
  const toast = useToast();
  const [text, setText] = useState("");
  const [zip, setZip] = useState<File | null>(null);
  const [overrideRoles, setOverrideRoles] = useState<string[] | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      let data: SkillsResponse;
      if (zip) {
        const form = new FormData();
        form.append("file", zip);
        form.append("confirm", "true");
        data = await api.postForm<SkillsResponse>("/api/admin/skills/import-zip", form);
      } else {
        data = await api.post<SkillsResponse>("/api/admin/skills/import", { text, roles: overrideRoles, confirm: true });
      }
      onImported(data);
      setText("");
      setZip(null);
      setOverrideRoles(null);
      toast("ok", "SKILL.md 를 가져왔습니다");
    } catch (e) {
      setError(errMsg(e, "가져오기에 실패했습니다"));
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  const toggleRole = (r: string) =>
    setOverrideRoles((cur) => {
      const base = cur ?? [];
      return base.includes(r) ? base.filter((x) => x !== r) : [...base, r];
    });

  return (
    <Card className="space-y-3 p-4">
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <FileUp className={ICON} aria-hidden /> SKILL.md 가져오기 <span className="text-xs font-normal text-faint">agentskills.io 형식 · 텍스트 또는 ZIP</span>
      </h2>
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <Field label="ZIP 파일 (선택)" hint="ZIP 안의 SKILL.md 하나만 읽습니다 — scripts/ 등 다른 파일은 열어보지도 않고 버립니다">
        <input
          type="file"
          accept=".zip"
          className={`${inputCls} py-1.5`}
          onChange={(e) => { setZip(e.target.files?.[0] ?? null); if (e.target.files?.[0]) setText(""); }}
        />
      </Field>
      {!zip && (
        <>
          <Field label="또는 SKILL.md 내용 붙여넣기" hint="--- 로 시작하는 frontmatter(name/description/license/roles)와 본문(지침)을 그대로 붙여 넣으세요">
            <textarea rows={8} className={`${inputCls} font-mono text-xs`} value={text} placeholder={SKILL_MD_EXAMPLE} onChange={(e) => setText(e.target.value)} />
          </Field>
          <fieldset>
            <legend className="mb-1 text-sm font-medium text-muted">붙일 역할 (비우면 SKILL.md 의 roles 를 씀)</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
              {roles.map((r) => (
                <label key={r} className="flex items-center gap-1.5">
                  <input type="checkbox" className="accent-[var(--accent)]" checked={!!overrideRoles?.includes(r)} onChange={() => toggleRole(r)} />
                  {AGENT_ROLE_LABEL[r] ?? r}
                </label>
              ))}
            </div>
          </fieldset>
        </>
      )}
      <button disabled={busy || (!zip && !text.trim())} className={btnSecondary} onClick={() => setConfirming(true)}>
        <FileUp className={ICON} aria-hidden /> 가져오기
      </button>
      {confirming && (
        <ConfirmDialog title="SKILL.md 를 가져올까요?" confirmLabel="가져오기" busy={busy} onConfirm={run} onCancel={() => setConfirming(false)}>
          같은 이름의 스킬이 있으면 덮어씁니다. scripts/ 같은 실행 파일은 가져오지 않습니다(텍스트 지침만).
        </ConfirmDialog>
      )}
    </Card>
  );
}

export function SkillsSection() {
  const toast = useToast();
  const [data, setData] = useState<SkillsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fresh, setFresh] = useState<Draft | null>(null);
  const [edits, setEdits] = useState<Record<string, Draft>>({});
  const [selected, setSelected] = useState<string | null>(null); // 파일트리에서 고른 스킬 이름(artex 레이아웃 참고, 2026-11)
  const [importing, setImporting] = useState(false);
  const [del, setDel] = useState<string | null>(null);
  const [saving, setSaving] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get<SkillsResponse>("/api/admin/skills").then(setData).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function save(d: Draft) {
    setBusy(true);
    setError(null);
    const wasNew = selected === "__new__";
    try {
      setData(await api.put<SkillsResponse>(`/api/admin/skills/${encodeURIComponent(d.name)}`, { description: d.description, instructions: d.instructions, roles: d.roles, enabled: d.enabled, confirm: true }));
      setEdits((m) => {
        const { [d.name]: _drop, ...rest } = m;
        return rest;
      });
      if (wasNew) {
        setFresh(null);
        setSelected(d.name);
      }
      toast("ok", "스킬을 저장했습니다");
    } catch (e) {
      setError(errMsg(e, "저장에 실패했습니다"));
    } finally {
      setBusy(false);
      setSaving(null);
    }
  }

  async function remove(name: string) {
    setBusy(true);
    try {
      setData(await api.post<SkillsResponse>(`/api/admin/skills/${encodeURIComponent(name)}/delete`, { confirm: true }));
      if (selected === name) setSelected(null);
      toast("ok", "스킬을 삭제했습니다");
    } catch (e) {
      setError(errMsg(e, "삭제에 실패했습니다"));
    } finally {
      setBusy(false);
      setDel(null);
    }
  }

  if (error && !data) return <Banner>{error}</Banner>;
  if (!data) return <SkeletonRows rows={3} />;
  const valid = (d: Draft) => NAME_RE.test(d.name) && d.instructions.trim().length > 0;
  const activeSkill = data.skills.find((s) => s.name === selected) ?? null;
  const activeDraft: Draft | null =
    selected === "__new__" ? fresh : selected ? (edits[selected] ?? (activeSkill ? fromSkill(activeSkill) : null)) : null;
  const dirty = selected === "__new__" ? true : activeSkill && activeDraft ? JSON.stringify(activeDraft) !== JSON.stringify(fromSkill(activeSkill)) : false;

  function selectExisting(name: string) {
    setSelected(name);
    setEdits((m) => (m[name] ? m : { ...m, [name]: fromSkill(data!.skills.find((s) => s.name === name)!) }));
  }
  function setActiveDraft(nd: Draft) {
    if (selected === "__new__") setFresh(nd);
    else if (selected) setEdits((m) => ({ ...m, [selected]: nd }));
  }

  return (
    <div className="space-y-4">
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <p className="text-xs text-faint">
        스킬은 선택한 역할의 시스템 프롬프트 뒤에 덧붙는 지침입니다(역할당 합계 약 6,000자까지, 이름순). 도구·권한을 추가하지 못하고 정책 검사는 그대로 적용됩니다. 저장 후 새로 하는 LLM 호출부터 적용됩니다.
      </p>
      <div className="flex gap-2">
        <button className={btnSecondary} onClick={() => setImporting(true)}>
          <FileUp className={ICON} aria-hidden /> 가져오기
        </button>
        <button className={btnPrimary} onClick={() => { setFresh(blank()); setSelected("__new__"); }}>
          <Plus className={ICON} aria-hidden /> 스킬 추가
        </button>
      </div>

      <div className="flex gap-4">
        <nav className="w-56 shrink-0 space-y-0.5 border-r border-line pr-3" aria-label="스킬 목록">
          {data.skills.length === 0 && <p className="text-xs text-faint">등록된 스킬이 없습니다.</p>}
          {data.skills.map((s) => (
            <button
              key={s.name}
              onClick={() => selectExisting(s.name)}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${selected === s.name ? "bg-raised text-ink" : "text-muted hover:bg-raised/60 hover:text-ink"}`}
            >
              <FileText className="h-3.5 w-3.5 shrink-0" aria-hidden />
              <span className="truncate font-mono">{s.name}</span>
              {!s.enabled && <span className="ml-auto shrink-0 text-xs text-faint">꺼짐</span>}
            </button>
          ))}
        </nav>
        <div className="min-w-0 flex-1">
          {!activeDraft ? (
            <div className="rounded-lg border border-dashed border-line px-6 py-10 text-center text-sm text-faint">
              왼쪽에서 스킬을 선택하거나 새로 추가하세요
            </div>
          ) : (
            <Card className="space-y-3 p-4">
              {selected !== "__new__" && activeSkill && (
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${activeSkill.enabled ? "bg-ok/15 text-ok" : "bg-raised text-muted"}`}>{activeSkill.enabled ? "사용" : "꺼짐"}</span>
                    {activeSkill.source === "import" && <span className="rounded-full bg-raised px-2 py-0.5 text-xs text-muted">가져옴{activeSkill.license ? ` · ${activeSkill.license}` : ""}</span>}
                  </div>
                  <span className="text-xs text-faint">{activeSkill.updated_by} · {formatTime(activeSkill.updated_at)}</span>
                </div>
              )}
              <SkillEditor draft={activeDraft} setDraft={setActiveDraft} roles={data.roles} isNew={selected === "__new__"} />
              <div className="flex gap-2">
                <button
                  disabled={busy || !dirty || !valid(activeDraft) || (selected === "__new__" && data.skills.some((s) => s.name === activeDraft.name))}
                  className={btnPrimary}
                  onClick={() => setSaving(activeDraft)}
                >
                  <Save className={ICON} aria-hidden /> 저장
                </button>
                {selected !== "__new__" && (
                  <button disabled={busy} className={btnDanger} onClick={() => setDel(selected!)}>
                    <Trash2 className={ICON} aria-hidden /> 삭제
                  </button>
                )}
              </div>
              {selected === "__new__" && data.skills.some((s) => s.name === activeDraft.name) && (
                <p className="text-xs text-warn">같은 이름의 스킬이 이미 있습니다.</p>
              )}
              {selected !== "__new__" && <UsagePanel name={selected!} />}
            </Card>
          )}
        </div>
      </div>

      {importing && (
        <Sheet title="SKILL.md 가져오기" onClose={() => setImporting(false)} widthClass="max-w-xl">
          <ImportSkillCard roles={data.roles} onImported={(d) => { setData(d); setImporting(false); toast("ok", "SKILL.md 를 가져왔습니다"); }} />
        </Sheet>
      )}
      {saving && (
        <ConfirmDialog title={`${saving.name} 스킬을 저장할까요?`} confirmLabel="저장" busy={busy} onConfirm={() => save(saving)} onCancel={() => setSaving(null)}>
          선택한 역할({saving.roles.map((r) => AGENT_ROLE_LABEL[r] ?? r).join(", ") || "없음"})의 이후 LLM 호출에 이 지침이 덧붙습니다. 변경 전/후가 감사 로그에 기록됩니다.
        </ConfirmDialog>
      )}
      {del && (
        <ConfirmDialog title={`${del} 스킬을 삭제할까요?`} confirmLabel="삭제" danger busy={busy} onConfirm={() => remove(del)} onCancel={() => setDel(null)}>
          삭제하면 이 지침은 더 이상 어떤 역할에도 붙지 않습니다.
        </ConfirmDialog>
      )}
    </div>
  );
}
