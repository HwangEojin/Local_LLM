// 인터셉트 구역: 도구 이름·인자가 매치하면 위험도·자율 모드와 무관하게 사람 승인을 강제하는 규칙(CLAUDE.md §8).
// 이 메뉴는 승인을 '추가'만 할 수 있다 — 거부를 끄거나 승인을 건너뛰게 만드는 기능 자체가 없다. 공유 파일
// (intercept_rules.json)의 규칙은 이름이 겹치면 보호가 약해질 수 있어 웹에서 바꾸거나 지울 수 없다(파일을 직접 수정).
import { useCallback, useEffect, useState } from "react";
import { Play, Plus, Save, Trash2 } from "lucide-react";

import { Banner, btnDanger, btnPrimary, btnSecondary, Card, ConfirmDialog, inputCls, SkeletonRows, useToast } from "@/components/ui";
import { api, InterceptRule } from "@/lib/api";
import { errMsg, Field, ICON } from "./parts";

type Draft = { name: string; tool_pattern: string; arg_pattern: string; enabled: boolean };
const NAME_RE = /^[A-Za-z0-9_.:-]{1,80}$/;
const blank = (): Draft => ({ name: "", tool_pattern: "", arg_pattern: "", enabled: true });
const fromRule = (r: InterceptRule): Draft => ({ name: r.name, tool_pattern: r.tool_pattern, arg_pattern: r.arg_pattern ?? "", enabled: r.enabled });
const validRegex = (s: string) => {
  try {
    new RegExp(s);
    return true;
  } catch {
    return false;
  }
};

function RuleEditor({ draft, setDraft, isNew }: { draft: Draft; setDraft: (d: Draft) => void; isNew: boolean }) {
  return (
    <div className="space-y-3">
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="이름" hint={isNew ? "영문·숫자·._:- (최대 80자), 만든 뒤에는 바꿀 수 없습니다" : undefined}>
          <input className={inputCls} value={draft.name} disabled={!isNew} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="sensitive-file-access" />
        </Field>
        <label className="mt-6 flex items-center gap-2 text-sm">
          <input type="checkbox" className="accent-[var(--accent)]" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />
          사용
        </label>
      </div>
      <Field label="도구 이름 정규식" hint={!draft.tool_pattern || validRegex(draft.tool_pattern) ? "도구 이름 전체에 대조합니다(예: ^fs\\.(read_file|list_dir)$)" : "정규식이 올바르지 않습니다"}>
        <input className={`${inputCls} font-mono text-sm`} value={draft.tool_pattern} onChange={(e) => setDraft({ ...draft, tool_pattern: e.target.value })} />
      </Field>
      <Field label="인자 정규식 (선택)" hint={!draft.arg_pattern || validRegex(draft.arg_pattern) ? "도구 인자를 JSON 문자열로 바꿔 대조합니다. 비우면 도구 이름만 봅니다" : "정규식이 올바르지 않습니다"}>
        <input className={`${inputCls} font-mono text-sm`} value={draft.arg_pattern} onChange={(e) => setDraft({ ...draft, arg_pattern: e.target.value })} />
      </Field>
    </div>
  );
}

function TestMatchBox() {
  const [tool, setTool] = useState("");
  const [args, setArgs] = useState("{}");
  const [result, setResult] = useState<{ ok: boolean; matched?: boolean; rule?: string | null; error?: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    let parsed: unknown;
    try {
      parsed = JSON.parse(args || "{}");
    } catch {
      setResult({ ok: false, error: "인자는 올바른 JSON 이어야 합니다" });
      return;
    }
    setBusy(true);
    try {
      const r = await api.post<{ matched: boolean; rule: string | null }>("/api/admin/intercept-rules/test-match", { tool, args: parsed });
      setResult({ ok: true, ...r });
    } catch (e) {
      setResult({ ok: false, error: e instanceof Error ? e.message : "테스트 실패" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="space-y-3 p-4">
      <h2 className="text-sm font-semibold">규칙 테스트</h2>
      <p className="text-xs text-faint">도구 이름과 인자를 넣어 지금 켜져 있는 규칙 중 어느 것이 걸리는지 미리 봅니다. 아무것도 저장하지 않습니다.</p>
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="도구 이름">
          <input className={`${inputCls} font-mono text-sm`} value={tool} onChange={(e) => setTool(e.target.value)} placeholder="fs.read_file" />
        </Field>
        <Field label="인자 (JSON)">
          <input className={`${inputCls} font-mono text-sm`} value={args} onChange={(e) => setArgs(e.target.value)} placeholder='{"path":"/etc/shadow"}' />
        </Field>
      </div>
      <button className={btnSecondary} disabled={busy || !tool.trim()} onClick={run}>
        <Play className={ICON} aria-hidden /> 테스트
      </button>
      {result && (
        result.ok ? (
          <p className={`text-sm ${result.matched ? "text-warn" : "text-ok"}`}>
            {result.matched ? `걸림 · 규칙 '${result.rule}' — 승인이 필요하게 됩니다` : "걸리는 규칙 없음 — 평소 위험도대로 처리됩니다"}
          </p>
        ) : (
          <p className="text-sm text-danger">{result.error}</p>
        )
      )}
    </Card>
  );
}

export function InterceptSection() {
  const toast = useToast();
  const [rules, setRules] = useState<InterceptRule[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fresh, setFresh] = useState<Draft | null>(null);
  const [edits, setEdits] = useState<Record<string, Draft>>({});
  const [del, setDel] = useState<string | null>(null);
  const [saving, setSaving] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get<InterceptRule[]>("/api/admin/intercept-rules").then(setRules).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function save(d: Draft) {
    setBusy(true);
    setError(null);
    try {
      setRules(await api.put<InterceptRule[]>(`/api/admin/intercept-rules/${encodeURIComponent(d.name)}`, {
        tool_pattern: d.tool_pattern, arg_pattern: d.arg_pattern || null, enabled: d.enabled, confirm: true,
      }));
      setEdits(({ [d.name]: _drop, ...rest }) => rest);
      if (fresh?.name === d.name) setFresh(null);
      toast("ok", "규칙을 저장했습니다");
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
      setRules(await api.post<InterceptRule[]>(`/api/admin/intercept-rules/${encodeURIComponent(name)}/delete`, { confirm: true }));
      toast("ok", "규칙을 삭제했습니다");
    } catch (e) {
      setError(errMsg(e, "삭제에 실패했습니다"));
    } finally {
      setBusy(false);
      setDel(null);
    }
  }

  if (error && !rules) return <Banner>{error}</Banner>;
  if (!rules) return <SkeletonRows rows={3} />;
  const valid = (d: Draft) => NAME_RE.test(d.name) && d.tool_pattern.trim() && validRegex(d.tool_pattern) && validRegex(d.arg_pattern);
  return (
    <div className="space-y-4">
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <p className="text-xs text-faint">
        매치하면 위험도·자율 모드와 무관하게 사람 승인이 필요해집니다(승인을 건너뛰게 만드는 기능은 없습니다). 저장 후 다음 도구 호출 판정부터 적용됩니다.
      </p>
      <TestMatchBox />
      {rules.map((r) => {
        const d = edits[r.name];
        const dirty = d && JSON.stringify(d) !== JSON.stringify(fromRule(r));
        return (
          <Card key={r.name} className="space-y-3 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm font-medium">{r.name}</span>
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${r.enabled ? "bg-ok/15 text-ok" : "bg-raised text-muted"}`}>{r.enabled ? "사용" : "꺼짐"}</span>
                <span className="text-xs text-faint">{r.source === "shared" ? "공유 파일" : "이 PC"}</span>
              </div>
            </div>
            {d ? (
              <>
                <RuleEditor draft={d} setDraft={(nd) => setEdits((m) => ({ ...m, [r.name]: nd }))} isNew={false} />
                <div className="flex gap-2">
                  <button disabled={busy || !dirty || !valid(d)} className={btnPrimary} onClick={() => setSaving(d)}>
                    <Save className={ICON} aria-hidden /> 저장
                  </button>
                  <button className="rounded-md px-3 py-1.5 text-sm text-muted hover:bg-raised" onClick={() => setEdits(({ [r.name]: _x, ...rest }) => rest)}>취소</button>
                  <button disabled={busy} className={btnDanger} onClick={() => setDel(r.name)}>
                    <Trash2 className={ICON} aria-hidden /> 삭제
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="font-mono text-xs text-muted">도구: {r.tool_pattern}{r.arg_pattern ? ` · 인자: ${r.arg_pattern}` : ""}</p>
                {r.source === "local" ? (
                  <button className="text-xs text-accent hover:underline" onClick={() => setEdits((m) => ({ ...m, [r.name]: fromRule(r) }))}>편집</button>
                ) : (
                  <p className="text-xs text-faint">공유 파일(intercept_rules.json)의 규칙이라 웹에서 바꿀 수 없습니다.</p>
                )}
              </>
            )}
          </Card>
        );
      })}
      {rules.length === 0 && <p className="text-sm text-faint">등록된 규칙이 없습니다.</p>}

      {fresh ? (
        <Card className="space-y-3 p-4">
          <h2 className="text-sm font-semibold">새 규칙</h2>
          <RuleEditor draft={fresh} setDraft={setFresh} isNew />
          <div className="flex gap-2">
            <button disabled={busy || !valid(fresh) || rules.some((r) => r.name === fresh.name)} className={btnPrimary} onClick={() => setSaving(fresh)}>
              <Save className={ICON} aria-hidden /> 저장
            </button>
            <button className="rounded-md px-3 py-1.5 text-sm text-muted hover:bg-raised" onClick={() => setFresh(null)}>취소</button>
          </div>
          {rules.some((r) => r.name === fresh.name) && <p className="text-xs text-warn">같은 이름의 규칙이 이미 있습니다.</p>}
        </Card>
      ) : (
        <button className={btnPrimary} onClick={() => setFresh(blank())}>
          <Plus className={ICON} aria-hidden /> 규칙 추가
        </button>
      )}

      {saving && (
        <ConfirmDialog title={`${saving.name} 규칙을 저장할까요?`} confirmLabel="저장" busy={busy} onConfirm={() => save(saving)} onCancel={() => setSaving(null)}>
          이후 도구 호출 판정부터 이 규칙이 적용됩니다. 변경 전/후가 감사 로그에 기록됩니다.
        </ConfirmDialog>
      )}
      {del && (
        <ConfirmDialog title={`${del} 규칙을 삭제할까요?`} confirmLabel="삭제" danger busy={busy} onConfirm={() => remove(del)} onCancel={() => setDel(null)}>
          이 규칙이 요구하던 승인이 더 이상 강제되지 않습니다.
        </ConfirmDialog>
      )}
    </div>
  );
}
