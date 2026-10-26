// 스킬 구역: 역할 프롬프트 뒤에 덧붙는 운영자 지침 묶음. 스킬은 LLM 이 일하는 방식을 다듬는 문구일 뿐이라 도구·권한을
// 늘리지 못하고(정책 검사는 서버가 매 호출 독립적으로 한다) 위의 규칙을 덮어쓰지 못한다고 프롬프트에도 명시돼 있다.
import { useCallback, useEffect, useState } from "react";
import { Plus, Save, Trash2 } from "lucide-react";

import { Banner, btnDanger, btnPrimary, Card, ConfirmDialog, inputCls, SkeletonRows, useToast } from "@/components/ui";
import { api, Skill, SkillsResponse } from "@/lib/api";
import { AGENT_ROLE_LABEL, formatTime } from "@/lib/labels";
import { errMsg, Field, ICON } from "./parts";

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

export function SkillsSection() {
  const toast = useToast();
  const [data, setData] = useState<SkillsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fresh, setFresh] = useState<Draft | null>(null);
  const [edits, setEdits] = useState<Record<string, Draft>>({});
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
    try {
      setData(await api.put<SkillsResponse>(`/api/admin/skills/${encodeURIComponent(d.name)}`, { description: d.description, instructions: d.instructions, roles: d.roles, enabled: d.enabled, confirm: true }));
      setEdits((m) => {
        const { [d.name]: _drop, ...rest } = m;
        return rest;
      });
      if (fresh && fresh.name === d.name) setFresh(null);
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
  return (
    <div className="space-y-4">
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <p className="text-xs text-faint">
        스킬은 선택한 역할의 시스템 프롬프트 뒤에 덧붙는 지침입니다(역할당 합계 약 6,000자까지, 이름순). 도구·권한을 추가하지 못하고 정책 검사는 그대로 적용됩니다. 저장 후 새로 하는 LLM 호출부터 적용됩니다.
      </p>
      {data.skills.map((s) => {
        const d = edits[s.name];
        const dirty = d && JSON.stringify(d) !== JSON.stringify(fromSkill(s));
        return (
          <Card key={s.name} className="space-y-3 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm font-medium">{s.name}</span>
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${s.enabled ? "bg-ok/15 text-ok" : "bg-raised text-muted"}`}>{s.enabled ? "사용" : "꺼짐"}</span>
                {s.roles.map((r) => (
                  <span key={r} className="rounded-full bg-raised px-2 py-0.5 text-xs text-muted">{AGENT_ROLE_LABEL[r] ?? r}</span>
                ))}
                {s.roles.length === 0 && <span className="text-xs text-warn">붙은 역할 없음</span>}
              </div>
              <span className="text-xs text-faint">{s.updated_by} · {formatTime(s.updated_at)}</span>
            </div>
            {d ? (
              <>
                <SkillEditor draft={d} setDraft={(nd) => setEdits((m) => ({ ...m, [s.name]: nd }))} roles={data.roles} isNew={false} />
                <div className="flex gap-2">
                  <button disabled={busy || !dirty || !valid(d)} className={btnPrimary} onClick={() => setSaving(d)}>
                    <Save className={ICON} aria-hidden /> 저장
                  </button>
                  <button className="rounded-md px-3 py-1.5 text-sm text-muted hover:bg-raised" onClick={() => setEdits(({ [s.name]: _x, ...rest }) => rest)}>취소</button>
                  <button disabled={busy} className={btnDanger} onClick={() => setDel(s.name)}>
                    <Trash2 className={ICON} aria-hidden /> 삭제
                  </button>
                </div>
              </>
            ) : (
              <>
                {s.description && <p className="text-xs text-muted">{s.description}</p>}
                <p className="line-clamp-3 whitespace-pre-wrap text-xs">{s.instructions}</p>
                <button className="text-xs text-accent hover:underline" onClick={() => setEdits((m) => ({ ...m, [s.name]: fromSkill(s) }))}>편집</button>
              </>
            )}
          </Card>
        );
      })}
      {data.skills.length === 0 && !fresh && <p className="text-sm text-faint">등록된 스킬이 없습니다.</p>}

      {fresh ? (
        <Card className="space-y-3 p-4">
          <h2 className="text-sm font-semibold">새 스킬</h2>
          <SkillEditor draft={fresh} setDraft={setFresh} roles={data.roles} isNew />
          <div className="flex gap-2">
            <button disabled={busy || !valid(fresh) || data.skills.some((s) => s.name === fresh.name)} className={btnPrimary} onClick={() => setSaving(fresh)}>
              <Save className={ICON} aria-hidden /> 저장
            </button>
            <button className="rounded-md px-3 py-1.5 text-sm text-muted hover:bg-raised" onClick={() => setFresh(null)}>취소</button>
          </div>
          {data.skills.some((s) => s.name === fresh.name) && <p className="text-xs text-warn">같은 이름의 스킬이 이미 있습니다. 목록에서 편집하세요.</p>}
        </Card>
      ) : (
        <button className={btnPrimary} onClick={() => setFresh(blank())}>
          <Plus className={ICON} aria-hidden /> 스킬 추가
        </button>
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
