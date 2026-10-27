// 알림 구역: finding 확정 알림을 받을 웹훅 채널 관리. 웹훅 주소 자체가 비밀(토큰이 경로에 있음)이라 목록에는 마스킹해서
// 보여 주고, 외부 주소로 보내게 만드는 변경(새 채널·주소 변경·켜기)은 별도 확인을 받는다. 공유 파일(notify.json)의 채널은
// 주소를 바꿀 수 없고 끄고 켜기만 이 PC 전용 파일(notify.local.json)에 덮어쓴다.
import { useCallback, useEffect, useState } from "react";
import { Plus, Send, Trash2 } from "lucide-react";

import { Banner, btnDanger, btnPrimary, btnSecondary, Card, ConfirmDialog, inputCls, SkeletonRows, useToast } from "@/components/ui";
import { api, NotificationChannel, NotificationHistoryEntry } from "@/lib/api";
import { formatTime } from "@/lib/labels";
import { errMsg, Field, ICON } from "./parts";

const SEVERITIES = ["critical", "high", "medium", "low", "info"];

// 서버(admin_extra._is_internal)와 같은 기준의 사전 판단. 최종 판정은 서버가 한다.
function looksInternal(url: string): boolean {
  try {
    const h = new URL(url).hostname.replace(/^\[|\]$/g, "").toLowerCase();
    return (
      h === "localhost" || h === "::1" || /^127\./.test(h) || /^10\./.test(h) || /^192\.168\./.test(h) || /^172\.(1[6-9]|2\d|3[01])\./.test(h)
    );
  } catch {
    return false;
  }
}

type Action =
  | { kind: "create"; name: string; url: string; minSeverity: string }
  | { kind: "enable"; ch: NotificationChannel }
  | { kind: "test"; ch: NotificationChannel }
  | { kind: "delete"; ch: NotificationChannel };

function HistoryCard() {
  const [items, setItems] = useState<NotificationHistoryEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.get<NotificationHistoryEntry[]>("/api/admin/notifications/history").then(setItems).catch((e) => setError(e.message));
  }, []);
  return (
    <Card className="space-y-3 p-4">
      <h2 className="text-sm font-semibold">전송 이력 <span className="text-xs font-normal text-faint">최근 100건</span></h2>
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      {!items ? (
        <p className="text-sm text-faint">불러오는 중…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-faint">아직 전송 기록이 없습니다.</p>
      ) : (
        <ul className="divide-y divide-line text-xs">
          {items.map((it) => (
            <li key={it.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5">
              <span className="tabular text-faint">{formatTime(it.ts)}</span>
              <span className={it.event === "notification_sent" ? "text-ok" : "text-danger"}>
                {it.event === "notification_sent" ? "전송됨" : "실패"}
              </span>
              <span className="font-mono">{it.details.channel}</span>
              {it.details.severity && <span className="text-muted">{it.details.severity}</span>}
              {it.run_code && <span className="font-mono text-faint">{it.run_code}</span>}
              {it.details.detail && <span className="text-faint">{it.details.detail}</span>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function NotificationsSection() {
  const toast = useToast();
  const [rows, setRows] = useState<NotificationChannel[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [minSeverity, setMinSeverity] = useState("");
  const [action, setAction] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Record<string, { ok: boolean; text: string }>>({});

  const load = useCallback(() => {
    api.get<NotificationChannel[]>("/api/admin/notifications").then(setRows).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function run(a: Action) {
    setBusy(true);
    setError(null);
    try {
      const base = "/api/admin/notifications";
      if (a.kind === "create") {
        setRows(await api.put<NotificationChannel[]>(`${base}/${encodeURIComponent(a.name)}`, {
          url: a.url, enabled: true, min_severity: a.minSeverity || null, confirm: true, confirm_external: !looksInternal(a.url),
        }));
        setName("");
        setUrl("");
        setMinSeverity("");
        toast("ok", "채널을 추가했습니다");
      } else if (a.kind === "enable") {
        setRows(await api.put<NotificationChannel[]>(`${base}/${encodeURIComponent(a.ch.name)}`, { enabled: true, confirm: true, confirm_external: a.ch.external }));
        toast("ok", `${a.ch.name} 채널을 켰습니다`);
      } else if (a.kind === "delete") {
        setRows(await api.post<NotificationChannel[]>(`${base}/${encodeURIComponent(a.ch.name)}/delete`, { confirm: true }));
        toast("ok", "채널을 삭제했습니다");
      } else {
        const r = await api.post<{ ok: boolean; detail: string }>(`${base}/${encodeURIComponent(a.ch.name)}/test`, { confirm: true });
        setResult((m) => ({ ...m, [a.ch.name]: { ok: r.ok, text: r.ok ? `전송됨 · ${r.detail}` : `실패 · ${r.detail}` } }));
      }
    } catch (e) {
      setError(errMsg(e, "처리하지 못했습니다"));
    } finally {
      setBusy(false);
      setAction(null);
    }
  }

  async function disable(ch: NotificationChannel) {
    setBusy(true);
    try {
      setRows(await api.put<NotificationChannel[]>(`/api/admin/notifications/${encodeURIComponent(ch.name)}`, { enabled: false, confirm: true }));
    } catch (e) {
      setError(errMsg(e, "처리하지 못했습니다"));
    } finally {
      setBusy(false);
    }
  }

  async function setSeverity(ch: NotificationChannel, severity: string) {
    setBusy(true);
    try {
      setRows(await api.put<NotificationChannel[]>(`/api/admin/notifications/${encodeURIComponent(ch.name)}`, { min_severity: severity, confirm: true }));
      toast("ok", `${ch.name} 채널의 최소 심각도를 바꿨습니다`);
    } catch (e) {
      setError(errMsg(e, "처리하지 못했습니다"));
    } finally {
      setBusy(false);
    }
  }

  if (error && !rows) return <Banner>{error}</Banner>;
  if (!rows) return <SkeletonRows rows={3} />;
  const canCreate = /^[A-Za-z0-9_.:-]{1,80}$/.test(name) && url.trim().length > 0;
  return (
    <div className="space-y-4">
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      <Card className="space-y-3 p-4">
        <h2 className="text-sm font-semibold">알림 채널 <span className="text-xs font-normal text-faint">finding 이 확정(CONFIRMED)될 때 웹훅으로 POST</span></h2>
        {rows.length === 0 ? (
          <p className="text-sm text-faint">등록된 채널이 없습니다. 알림은 부수 기능이라 채널이 없어도 진단·판정에는 영향이 없습니다.</p>
        ) : (
          <ul className="divide-y divide-line">
            {rows.map((c) => (
              <li key={c.name} className="py-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm font-medium">{c.name}</span>
                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${c.enabled ? "bg-ok/15 text-ok" : "bg-raised text-muted"}`}>{c.enabled ? "켜짐" : "꺼짐"}</span>
                      <span className="text-xs text-faint">{c.source === "shared" ? "공유 파일" : "이 PC"}</span>
                      {c.external && <span className="rounded-full bg-warn/15 px-2 py-0.5 text-xs font-medium text-warn">외부 주소</span>}
                    </div>
                    <div className="mt-0.5 font-mono text-xs text-muted">{c.url_masked}</div>
                    <label className="mt-1 flex items-center gap-1.5 text-xs text-muted">
                      최소 심각도
                      <select className={`${inputCls} w-28 py-0.5`} value={c.min_severity ?? ""} disabled={busy} onChange={(e) => setSeverity(c, e.target.value)}>
                        <option value="">전부</option>
                        {SEVERITIES.map((s) => (
                          <option key={s} value={s}>{s}</option>
                        ))}
                      </select>
                    </label>
                    {result[c.name] && (
                      <div className={`mt-0.5 text-xs ${result[c.name].ok ? "text-ok" : "text-danger"}`}>{result[c.name].text}</div>
                    )}
                  </div>
                  <div className="flex shrink-0 flex-wrap gap-2">
                    <button disabled={busy} className={btnSecondary} onClick={() => setAction({ kind: "test", ch: c })}>
                      <Send className={ICON} aria-hidden /> 테스트 전송
                    </button>
                    <button disabled={busy} className={btnSecondary} onClick={() => (c.enabled ? disable(c) : c.external ? setAction({ kind: "enable", ch: c }) : run({ kind: "enable", ch: c }))}>
                      {c.enabled ? "끄기" : "켜기"}
                    </button>
                    {c.source === "local" && (
                      <button disabled={busy} className={btnDanger} onClick={() => setAction({ kind: "delete", ch: c })}>
                        <Trash2 className={ICON} aria-hidden /> 삭제
                      </button>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="space-y-3 p-4">
        <h2 className="text-sm font-semibold">채널 추가</h2>
        <div className="grid gap-4 md:grid-cols-3">
          <Field label="이름" hint="영문·숫자·._:- (최대 80자)">
            <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="slack-security" />
          </Field>
          <Field label="웹훅 주소" hint="http(s)://… 저장 후에는 마스킹돼 다시 볼 수 없습니다">
            <input className={inputCls} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://hooks.example.com/…" autoComplete="off" />
          </Field>
          <Field label="최소 심각도 (선택)" hint="비우면 모든 심각도를 보냅니다">
            <select className={inputCls} value={minSeverity} onChange={(e) => setMinSeverity(e.target.value)}>
              <option value="">전부</option>
              {SEVERITIES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </Field>
        </div>
        <button disabled={!canCreate || busy} className={btnPrimary} onClick={() => setAction({ kind: "create", name, url: url.trim(), minSeverity })}>
          <Plus className={ICON} aria-hidden /> 추가
        </button>
        <p className="text-xs text-faint">
          알림에는 진단 ID·finding ID·제목·심각도·확정 주체가 담깁니다. 이 PC 전용 파일(notify.local.json, Git 제외)에 저장되고 변경은 감사 로그에 기록됩니다(주소는 마스킹).
        </p>
      </Card>

      <HistoryCard />

      {action && (
        <ConfirmDialog
          title={
            action.kind === "create" ? `${action.name} 채널을 추가할까요?` :
            action.kind === "enable" ? `${action.ch.name} 채널을 켤까요?` :
            action.kind === "test" ? `${action.ch.name} 으로 테스트를 보낼까요?` : `${action.ch.name} 채널을 삭제할까요?`
          }
          confirmLabel={action.kind === "create" ? "추가" : action.kind === "enable" ? "켜기" : action.kind === "test" ? "전송" : "삭제"}
          danger={action.kind === "delete"}
          busy={busy}
          onConfirm={() => run(action)}
          onCancel={() => setAction(null)}
        >
          {action.kind === "create" && (looksInternal(action.url)
            ? "내부(루프백·사설) 주소로 finding 확정 알림을 보냅니다."
            : "외부 주소입니다. finding 확정 시 진단 ID·제목·심각도 같은 정보가 이 주소로 전송됩니다. 신뢰하는 곳인지 확인하세요.")}
          {action.kind === "enable" && "외부 주소로 finding 확정 알림이 전송되기 시작합니다."}
          {action.kind === "test" && "진단 데이터 없이 테스트 이벤트 한 번만 이 채널 주소로 전송합니다. 결과는 감사 로그에 남습니다."}
          {action.kind === "delete" && "이 PC 전용 채널을 삭제합니다. 공유 파일에 같은 이름의 채널이 있으면 그 채널로 되돌아갑니다."}
        </ConfirmDialog>
      )}
    </div>
  );
}
