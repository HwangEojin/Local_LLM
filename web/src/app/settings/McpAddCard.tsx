// MCP 서버 추가: 연결 테스트·도구 발견 → 운영자가 고른 도구만 위험도·범위 검사 인자와 함께 등록.
// 원격(sse) 서버만 웹에서 추가한다(stdio 명령 등록은 이 PC 에서 임의 명령을 실행하게 만들어 파일로만). 도구는 자동 등록하지 않으며
// 등록된 도구도 매 호출 서버의 정책 검사(위험도·scope_args·승인)를 그대로 통과해야 한다.
import { useState } from "react";
import { Plug, Plus, Search } from "lucide-react";

import { Banner, btnPrimary, btnSecondary, Card, ConfirmDialog, inputCls } from "@/components/ui";
import { api, DiscoveredTool, McpDiscovery, McpServer } from "@/lib/api";
import { errMsg, Field, ICON } from "./parts";

type Pick = { on: boolean; risk: string; scope: Record<string, string> };
const RISKS = ["read", "write", "dangerous"];
const NAME_RE = /^[A-Za-z0-9_.-]{1,40}$/;

const stringProps = (t: DiscoveredTool) =>
  Object.entries(t.input_schema.properties ?? {}).filter(([, v]) => v?.type === "string").map(([k]) => k);

export function McpAddCard({ onAdded }: { onAdded: (servers: McpServer[]) => void }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("http://127.0.0.1:");
  const [timeout, setTimeoutS] = useState(60);
  const [desc, setDesc] = useState("");
  const [found, setFound] = useState<{ server: string; tools: DiscoveredTool[] } | null>(null);
  const [picks, setPicks] = useState<Record<string, Pick>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  async function discover() {
    setBusy(true);
    setError(null);
    setFound(null);
    try {
      const r = await api.post<McpDiscovery>("/api/admin/mcp/discover", { url: url.trim(), timeout_s: 30 });
      if (!r.ok) {
        setError(`연결하지 못했습니다: ${r.error}`);
        return;
      }
      setFound({ server: r.server, tools: r.tools });
      // 기본: 아무것도 고르지 않음. 위험도는 write, 범위 검사 인자는 이름으로 짐작한 후보를 미리 채운다(운영자가 확인)
      setPicks(Object.fromEntries(r.tools.map((t) => [t.name, { on: false, risk: "write", scope: { ...t.suggested_scope } }])));
    } catch (e) {
      setError(errMsg(e, "발견에 실패했습니다"));
    } finally {
      setBusy(false);
    }
  }

  const chosen = found ? found.tools.filter((t) => picks[t.name]?.on) : [];
  const unscopedReads = chosen.filter((t) => picks[t.name].risk === "read" && Object.keys(picks[t.name].scope).length === 0);

  async function register() {
    setBusy(true);
    setError(null);
    try {
      const servers = await api.post<McpServer[]>(`/api/admin/mcp/${encodeURIComponent(name)}/register`, {
        url: url.trim(),
        description: desc,
        timeout_s: timeout,
        enabled: true,
        confirm: true,
        confirm_unscoped_read: unscopedReads.length > 0,
        tools: chosen.map((t) => ({ name: t.name, risk: picks[t.name].risk, scope_args: picks[t.name].scope })),
      });
      onAdded(servers);
      setFound(null);
      setName("");
      setDesc("");
    } catch (e) {
      setError(errMsg(e, "등록에 실패했습니다"));
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  const setPick = (n: string, patch: Partial<Pick>) => setPicks((p) => ({ ...p, [n]: { ...p[n], ...patch } }));
  const setScope = (n: string, arg: string, kind: string) =>
    setPicks((p) => {
      const { [arg]: _drop, ...rest } = p[n].scope;
      return { ...p, [n]: { ...p[n], scope: kind ? { ...rest, [arg]: kind } : rest } };
    });

  return (
    <Card className="space-y-3 p-4">
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <Plug className={ICON} aria-hidden /> MCP 서버 추가 <span className="text-xs font-normal text-faint">원격(sse) · 로컬 주소만</span>
      </h2>
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <div className="grid gap-4 md:grid-cols-3">
        <Field label="이름" hint="영문·숫자·._- (최대 40자), 도구는 mcp:이름/도구 로 등록됩니다">
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="my-scanner" />
        </Field>
        <div className="md:col-span-2">
          <Field label="서버 주소 (SSE)" hint="예: http://127.0.0.1:9876/sse — 로컬 주소만 웹에서 추가할 수 있습니다">
            <input className={inputCls} value={url} onChange={(e) => setUrl(e.target.value)} />
          </Field>
        </div>
      </div>
      <button className={btnSecondary} disabled={busy || !/^https?:\/\/.+/.test(url.trim())} onClick={discover}>
        <Search className={ICON} aria-hidden /> {busy && !found ? "연결 중…" : "연결 테스트 · 도구 발견"}
      </button>

      {found && (
        <div className="space-y-3">
          <p className="text-sm text-ok">연결됨 · {found.server} · 도구 {found.tools.length}개 발견 — 등록할 도구만 고르세요(고르지 않은 도구는 등록되지 않아 호출할 수 없습니다)</p>
          <ul className="divide-y divide-line rounded-md border border-line">
            {found.tools.map((t) => {
              const p = picks[t.name];
              const props = stringProps(t);
              return (
                <li key={t.name} className="space-y-2 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <label className="flex items-center gap-2 text-sm">
                      <input type="checkbox" className="accent-[var(--accent)]" disabled={!t.schema_ok} checked={!!p?.on} onChange={(e) => setPick(t.name, { on: e.target.checked })} />
                      <span className="font-mono">{t.name}</span>
                    </label>
                    <select className={`${inputCls} w-28`} value={p?.risk} disabled={!p?.on} aria-label={`${t.name} 위험도`} onChange={(e) => setPick(t.name, { risk: e.target.value })}>
                      {RISKS.map((r) => (
                        <option key={r} value={r}>{r}</option>
                      ))}
                    </select>
                  </div>
                  {t.description && <p className="line-clamp-2 text-xs text-muted">{t.description}</p>}
                  {!t.schema_ok && <p className="text-xs text-danger">{t.schema_error} — 검증이 헐거워질 수 있어 등록할 수 없습니다.</p>}
                  {t.schema_ok && p?.on && (
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
                      <span>범위 검사 인자:</span>
                      {props.length === 0 && <span className="text-faint">문자열 인자 없음</span>}
                      {props.map((arg) => (
                        <label key={arg} className="flex items-center gap-1">
                          <span className="font-mono">{arg}</span>
                          <select className={`${inputCls} w-24 py-0.5`} value={p.scope[arg] ?? ""} onChange={(e) => setScope(t.name, arg, e.target.value)} aria-label={`${t.name}.${arg} 범위 종류`}>
                            <option value="">검사 안 함</option>
                            <option value="url">url</option>
                            <option value="host">host</option>
                            <option value="path">path</option>
                          </select>
                        </label>
                      ))}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          <Field label="설명 (선택)">
            <input className={inputCls} value={desc} maxLength={300} onChange={(e) => setDesc(e.target.value)} />
          </Field>
          <button className={btnPrimary} disabled={busy || chosen.length === 0 || !NAME_RE.test(name)} onClick={() => setConfirming(true)}>
            <Plus className={ICON} aria-hidden /> 선택한 도구 {chosen.length}개 등록
          </button>
        </div>
      )}
      <p className="text-xs text-faint">
        stdio(명령 실행) 서버는 이 PC에서 명령을 실행하게 되므로 웹에서 추가할 수 없습니다 — config/mcp_servers.local.json 을 직접 수정하세요.
        등록은 이 PC 전용 파일에 저장되고 감사 로그에 기록됩니다. 도구 스키마는 서버가 알려 준 값을 저장 시점에 다시 받아 씁니다.
      </p>

      {confirming && (
        <ConfirmDialog title={`${name} 서버의 도구 ${chosen.length}개를 등록할까요?`} confirmLabel="등록" danger={unscopedReads.length > 0} busy={busy} onConfirm={register} onCancel={() => setConfirming(false)}>
          등록한 도구는 새 진단에서 호출 후보가 되며, 호출은 여전히 범위·위험도·승인 검사를 통과해야 합니다.
          {unscopedReads.length > 0 && (
            <span className="mt-2 block text-danger">
              범위 검사 인자 없이 read 로 등록하는 도구: {unscopedReads.map((t) => t.name).join(", ")} — 대상 범위 확인 없이, 승인 없이 실행됩니다.
            </span>
          )}
        </ConfirmDialog>
      )}
    </Card>
  );
}
