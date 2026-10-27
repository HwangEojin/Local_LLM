"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ChevronDown, Download, Pause, Play, RefreshCw, Search } from "lucide-react";

import { AuthGate } from "@/components/AuthGate";
import { Header } from "@/components/Header";
import { Banner, btnSecondary, Card, Empty, inputCls, SkeletonRows } from "@/components/ui";
import { api, auditExportUrl, AuditEvent } from "@/lib/api";
import { formatTime } from "@/lib/labels";
import { usePolling } from "@/lib/usePolling";

const PAGE = 100;
const ICON = "h-4 w-4 shrink-0";

// 모르는 이벤트는 원문 이름을 그대로 보여 준다.
const EVENT_LABEL: Record<string, string> = {
  admin_scope_changed: "진단 대상 변경",
  admin_workbench_changed: "제외·예산·제한 변경",
  admin_settings_changed: "LLM·호출 제한 변경",
  admin_mcp_toggled: "MCP 서버 활성 변경",
  admin_kill_switch_engaged: "중지 스위치 켬",
  admin_kill_switch_released: "중지 스위치 해제",
  run_created: "진단 생성",
  run_deleted: "진단 삭제",
  run_restored: "진단 복원",
  approval_recorded: "승인/거부 기록",
  retry_requested: "작업 재시도 요청",
  finding_reviewed: "발견 사항 판정",
  checklist_imported_to_db: "체크리스트 가져오기",
};

const isAdmin = (e: string) => e.startsWith("admin_");

function actorOf(d: Record<string, unknown>): string {
  const v = d.actor ?? d.operator ?? d.approver;
  return typeof v === "string" ? v : "-";
}

function AuditBody() {
  const [items, setItems] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [event, setEvent] = useState("");
  const [run, setRun] = useState("");
  const [applied, setApplied] = useState({ event: "", run: "" });
  const [more, setMore] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const [live, setLive] = useState(false);

  const query = useCallback(
    (before?: number) => {
      const p = new URLSearchParams({ limit: String(PAGE) });
      if (applied.event.trim()) p.set("event", applied.event.trim());
      if (applied.run.trim()) p.set("run", applied.run.trim());
      if (before) p.set("before", String(before));
      return api.get<AuditEvent[]>(`/api/admin/audit?${p.toString()}`);
    },
    [applied],
  );

  const load = useCallback(() => {
    query()
      .then((d) => {
        setItems(d);
        setMore(d.length === PAGE);
        setError(null);
      })
      .catch((e) => setError(e.message));
  }, [query]);
  useEffect(load, [load]);
  usePolling(load, 4000, live); // 실시간: 맨 위부터 다시 불러오므로 "더 보기"로 쌓은 다음 페이지는 접힌다

  async function loadMore() {
    if (!items?.length) return;
    try {
      const d = await query(items[items.length - 1].id);
      setItems([...items, ...d]);
      setMore(d.length === PAGE);
    } catch (e) {
      setError(e instanceof Error ? e.message : "불러오지 못했습니다");
    }
  }

  function adminOnly() {
    setEvent("admin_");
    setRun("");
    setApplied({ event: "admin_", run: "" });
  }

  return (
    <main className="mx-auto w-full max-w-6xl p-6">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">감사 로그</h1>
        <div className="flex items-center gap-2">
          <button onClick={() => setLive((v) => !v)} className={btnSecondary} aria-pressed={live}>
            {live ? <Pause className={ICON} aria-hidden /> : <Play className={ICON} aria-hidden />}
            {live ? "실시간 중지" : "실시간"}
          </button>
          <button onClick={load} className={btnSecondary}>
            <RefreshCw className={ICON} aria-hidden /> 새로고침
          </button>
          <a href={auditExportUrl(applied.event, applied.run, "csv")} className={btnSecondary} download>
            <Download className={ICON} aria-hidden /> CSV
          </a>
          <a href={auditExportUrl(applied.event, applied.run, "json")} className={btnSecondary} download>
            <Download className={ICON} aria-hidden /> JSON
          </a>
        </div>
      </div>
      <p className="mb-5 text-sm text-muted">진단 생성, 승인, 판정, 설정 변경 같은 모든 기록입니다. 이 기록은 수정하거나 삭제할 수 없습니다.</p>

      <form
        className="mb-4 flex flex-wrap items-center gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied({ event, run });
        }}
      >
        <input value={event} onChange={(e) => setEvent(e.target.value)} placeholder="이벤트 이름 (예: admin_, approval)" aria-label="이벤트 검색" className={`${inputCls} !w-64`} />
        <input value={run} onChange={(e) => setRun(e.target.value)} placeholder="진단 ID (예: BASE-20261023-01)" aria-label="진단 ID 필터" className={`${inputCls} !w-64`} />
        <button type="submit" className={btnSecondary}>
          <Search className={ICON} aria-hidden /> 검색
        </button>
        <button type="button" onClick={adminOnly} className="text-sm text-accent hover:underline">
          설정 변경만 보기
        </button>
      </form>

      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      {!items ? (
        <SkeletonRows rows={8} />
      ) : items.length === 0 ? (
        <Empty title="조건에 맞는 기록이 없습니다" />
      ) : (
        <>
          <Card className="overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-raised text-left text-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-medium">시각</th>
                  <th scope="col" className="px-4 py-2 font-medium">이벤트</th>
                  <th scope="col" className="px-4 py-2 font-medium">진단</th>
                  <th scope="col" className="px-4 py-2 font-medium">수행자</th>
                  <th scope="col" className="px-4 py-2 font-medium"><span className="sr-only">상세</span></th>
                </tr>
              </thead>
              <tbody>
                {items.map((a) => (
                  <Fragment key={a.id}>
                    <tr className="border-t border-line hover:bg-raised/60">
                      <td className="tabular whitespace-nowrap px-4 py-2 text-muted">{formatTime(a.ts)}</td>
                      <td className="px-4 py-2">
                        <span className={isAdmin(a.event) ? "font-medium text-warn" : ""}>{EVENT_LABEL[a.event] ?? a.event}</span>
                        {EVENT_LABEL[a.event] && <span className="ml-2 font-mono text-xs text-faint">{a.event}</span>}
                      </td>
                      <td className="px-4 py-2">
                        {a.run_code ? (
                          <Link href={`/runs/${a.run_code}`} className="font-mono text-xs text-accent hover:underline">
                            {a.run_code}
                          </Link>
                        ) : (
                          <span className="text-faint">-</span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-muted">{actorOf(a.details)}</td>
                      <td className="px-4 py-2 text-right">
                        <button onClick={() => setOpen(open === a.id ? null : a.id)} aria-expanded={open === a.id} aria-label="상세 보기" className="rounded p-1 text-muted hover:bg-raised hover:text-ink">
                          <ChevronDown className={`${ICON} transition-transform ${open === a.id ? "rotate-180" : ""}`} aria-hidden />
                        </button>
                      </td>
                    </tr>
                    {open === a.id && (
                      <tr className="border-t border-line bg-canvas">
                        <td colSpan={5} className="px-4 py-3">
                          <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] text-muted">{JSON.stringify(a.details, null, 2)}</pre>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </Card>
          {more && (
            <div className="mt-3 text-center">
              <button onClick={loadMore} className={btnSecondary}>
                더 보기
              </button>
            </div>
          )}
        </>
      )}
    </main>
  );
}

export default function AuditPage() {
  return (
    <AuthGate>
      <Header />
      <AuditBody />
    </AuthGate>
  );
}
