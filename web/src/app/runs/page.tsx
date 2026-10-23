"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ChevronLeft, ChevronRight, Plus, RotateCcw, Trash2 } from "lucide-react";

import { AuthGate } from "@/components/AuthGate";
import { Header } from "@/components/Header";
import { StatusBadge } from "@/components/StatusBadge";
import { Banner, btnDanger, btnGhost, btnPrimary, btnSecondary, Card, ConfirmDialog, Empty, inputCls, SkeletonRows, useToast } from "@/components/ui";
import { api, ApiError, BulkDeleteResult, RUN_STATUSES, RunList } from "@/lib/api";
import { AUTONOMY_LABEL, describe, formatTime } from "@/lib/labels";
import { usePolling } from "@/lib/usePolling";

const PAGE_SIZES = [20, 50, 100];
const ICON = "h-4 w-4 shrink-0";
type SortKey = "created_at" | "status" | "operator";

function RunsTable() {
  const toast = useToast();
  const [data, setData] = useState<RunList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [qInput, setQInput] = useState(""); // 입력 중인 값(디바운스 전) — 검색창이 매 글자마다 다시 쿼리하지 않게 분리
  const [statuses, setStatuses] = useState<string[]>([]);
  const [sort, setSort] = useState<SortKey>("created_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [offset, setOffset] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  const [showDeleted, setShowDeleted] = useState(false);
  const [showTests, setShowTests] = useState(false); // 자동 테스트가 만든 진단(운영자 pytest*)은 기본으로 숨긴다
  const [selected, setSelected] = useState<string[]>([]);
  const [dialog, setDialog] = useState<"delete" | "restore" | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const id = setTimeout(() => {
      setQ(qInput);
      setOffset(0);
      setSelected([]);
    }, 300); // 검색어 입력 디바운스
    return () => clearTimeout(id);
  }, [qInput]);

  const load = useCallback(() => {
    const params = new URLSearchParams({
      sort,
      order,
      limit: String(pageSize),
      offset: String(offset),
      deleted: showDeleted ? "only" : "exclude",
      tests: showTests ? "show" : "hide",
    });
    if (q.trim()) params.set("q", q.trim());
    if (statuses.length) params.set("status", statuses.join(","));
    api
      .get<RunList>(`/api/runs?${params.toString()}`)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(e.message));
  }, [q, statuses, sort, order, offset, pageSize, showDeleted, showTests]);

  useEffect(load, [load]);
  usePolling(load, 15000); // 실행 중인 Run 의 상태·발견 수가 목록에서도 따라오게

  // 필터·페이지·보기가 바뀌면 선택을 비운다(보이지 않는 항목이 선택된 채 남아 삭제되는 일을 막는다).
  const resetView = (fn: () => void) => {
    fn();
    setOffset(0);
    setSelected([]);
  };

  function toggleStatus(s: string) {
    resetView(() => setStatuses((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s])));
  }

  function toggleSort(col: SortKey) {
    resetView(() => {
      if (sort === col) {
        setOrder((o) => (o === "asc" ? "desc" : "asc"));
      } else {
        setSort(col);
        setOrder("desc");
      }
    });
  }

  const sortMark = (col: SortKey) => (sort === col ? (order === "asc" ? " ▲" : " ▼") : "");
  const ariaSort = (col: SortKey) => (sort === col ? (order === "asc" ? "ascending" : "descending") : "none");

  const runs = data?.runs ?? null;
  const total = data?.total ?? 0;
  const page = Math.floor(offset / pageSize) + 1;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const filtering = statuses.length > 0 || q.trim() !== "";
  const allOnPage = !!runs?.length && runs.every((r) => selected.includes(r.run_code));
  const selectedRuns = useMemo(() => (runs ?? []).filter((r) => selected.includes(r.run_code)), [runs, selected]);

  async function bulk(reason: string) {
    setBusy(true);
    try {
      if (dialog === "delete") {
        const res = await api.post<BulkDeleteResult>("/api/runs/bulk-delete", { refs: selected, reason, confirm: true });
        toast(res.skipped.length ? "danger" : "ok", `${res.deleted.length}건을 삭제했습니다${res.skipped.length ? ` · ${res.skipped.length}건은 건너뜀(실행 중 등)` : ""}`);
      } else {
        for (const ref of selected) await api.post(`/api/runs/${ref}/restore`, { reason: reason || "복원" });
        toast("ok", `${selected.length}건을 복원했습니다`);
      }
      setSelected([]);
      load();
    } catch (e) {
      toast("danger", e instanceof ApiError ? e.message : "처리에 실패했습니다");
    } finally {
      setBusy(false);
      setDialog(null);
    }
  }

  const sortable = (col: SortKey, label: string) => (
    <th scope="col" aria-sort={ariaSort(col)} className="px-4 py-2 font-medium">
      <button onClick={() => toggleSort(col)} className="hover:text-ink">
        {label}
        {sortMark(col)}
      </button>
    </th>
  );

  return (
    <main className="mx-auto w-full max-w-6xl p-6">
      <div className="mb-5 flex items-center justify-between">
        <h1 className="text-lg font-semibold">{showDeleted ? "삭제된 진단" : "진단 목록"}</h1>
        <Link href="/runs/new" className={btnPrimary}>
          <Plus className={ICON} aria-hidden /> 새 진단
        </Link>
      </div>

      {error && <Banner onClose={() => setError(null)}>목록을 불러오지 못했습니다: {error}</Banner>}

      <div className="mb-3 flex flex-wrap items-center gap-3">
        <input
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
          placeholder="진단 ID(예: BASE-20261023-01) 또는 운영자 검색"
          aria-label="진단 검색"
          className={`${inputCls} !w-64`}
        />
        <div className="flex flex-wrap items-center gap-1.5">
          {RUN_STATUSES.map((s) => {
            const on = statuses.includes(s);
            return (
              <button
                key={s}
                onClick={() => toggleStatus(s)}
                aria-pressed={on}
                className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                  on ? "border-accent bg-accent/15 text-accent" : "border-line text-muted hover:border-faint hover:text-ink"
                }`}
              >
                {describe(s).label}
              </button>
            );
          })}
          {filtering && (
            <button
              onClick={() =>
                resetView(() => {
                  setStatuses([]);
                  setQInput("");
                  setQ("");
                })
              }
              className={btnGhost}
            >
              필터 초기화
            </button>
          )}
        </div>
        <div className="ml-auto flex items-center gap-4 text-sm text-muted">
          <label className="flex items-center gap-2" title="운영자 이름이 pytest 로 시작하는 진단">
            <input type="checkbox" className="accent-[var(--accent)]" checked={showTests} onChange={(e) => resetView(() => setShowTests(e.target.checked))} />
            테스트 진단 포함
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" className="accent-[var(--accent)]" checked={showDeleted} onChange={(e) => resetView(() => setShowDeleted(e.target.checked))} />
            삭제된 진단 보기
          </label>
        </div>
      </div>

      {selected.length > 0 && (
        <div className="mb-3 flex items-center justify-between gap-3 rounded-md border border-accent/40 bg-accent/10 px-3 py-2 text-sm">
          <span>{selected.length}건 선택됨</span>
          <div className="flex items-center gap-2">
            <button onClick={() => setSelected([])} className={btnGhost}>
              선택 해제
            </button>
            {showDeleted ? (
              <button onClick={() => setDialog("restore")} className={btnSecondary}>
                <RotateCcw className={ICON} aria-hidden /> 복원
              </button>
            ) : (
              <button onClick={() => setDialog("delete")} className={btnDanger}>
                <Trash2 className={ICON} aria-hidden /> 삭제
              </button>
            )}
          </div>
        </div>
      )}

      {!runs ? (
        <SkeletonRows rows={6} />
      ) : runs.length === 0 ? (
        showDeleted ? (
          <Empty title="삭제된 진단이 없습니다" />
        ) : filtering ? (
          <Empty title="조건에 맞는 진단이 없습니다" hint="검색어나 상태 필터를 바꿔 보세요." />
        ) : (
          <Empty title="아직 진단이 없습니다" hint="‘새 진단’에서 대상을 골라 첫 진단을 시작하세요." />
        )
      ) : (
        <>
          <Card className="overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-raised text-left text-muted">
                <tr>
                  <th scope="col" className="w-10 px-4 py-2">
                    <input
                      type="checkbox"
                      className="accent-[var(--accent)]"
                      aria-label="현재 페이지 전체 선택"
                      checked={allOnPage}
                      onChange={(e) => setSelected(e.target.checked ? runs.map((r) => r.run_code) : [])}
                    />
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    진단 ID
                  </th>
                  {sortable("status", "상태")}
                  {sortable("operator", "운영자")}
                  <th scope="col" className="px-4 py-2 font-medium">
                    방식
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    자율성
                  </th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">
                    발견 사항
                  </th>
                  {sortable("created_at", "생성 시각")}
                  {showDeleted && (
                    <th scope="col" className="px-4 py-2 font-medium">
                      삭제 사유
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} className={`border-t border-line hover:bg-raised/60 ${selected.includes(r.run_code) ? "bg-accent/5" : ""}`}>
                    <td className="px-4 py-2">
                      <input
                        type="checkbox"
                        className="accent-[var(--accent)]"
                        aria-label={`${r.run_code} 선택`}
                        checked={selected.includes(r.run_code)}
                        onChange={(e) => setSelected((s) => (e.target.checked ? [...s, r.run_code] : s.filter((x) => x !== r.run_code)))}
                      />
                    </td>
                    <td className="px-4 py-2">
                      <Link href={`/runs/${r.run_code}`} className="font-mono text-xs text-accent hover:underline">
                        {r.run_code}
                      </Link>
                    </td>
                    <td className="px-4 py-2">
                      <StatusBadge value={r.status} />
                    </td>
                    <td className="px-4 py-2">{r.operator}</td>
                    <td className="px-4 py-2 text-muted">{r.has_checklist ? "체크리스트" : "자율 탐색"}</td>
                    <td className="px-4 py-2 text-muted">{AUTONOMY_LABEL[r.autonomy] ?? r.autonomy}</td>
                    <td className="px-4 py-2 text-right">{r.findings_count}</td>
                    <td className="px-4 py-2 text-muted">{formatTime(r.created_at)}</td>
                    {showDeleted && <td className="px-4 py-2 text-muted">{r.delete_reason}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-sm text-muted">
            <span className="flex items-center gap-3">
              <span>
                전체 {total}건 중 {offset + 1}–{Math.min(offset + pageSize, total)}
              </span>
              <label className="flex items-center gap-1.5">
                페이지당
                <select
                  value={pageSize}
                  onChange={(e) => resetView(() => setPageSize(Number(e.target.value)))}
                  className="rounded-md border border-line bg-canvas px-2 py-1 text-sm text-ink"
                  aria-label="페이지당 개수"
                >
                  {PAGE_SIZES.map((n) => (
                    <option key={n} value={n}>
                      {n}건
                    </option>
                  ))}
                </select>
              </label>
            </span>
            <div className="flex items-center gap-2">
              <button
                disabled={page <= 1}
                onClick={() => {
                  setOffset((o) => Math.max(0, o - pageSize));
                  setSelected([]);
                }}
                className={btnSecondary}
              >
                <ChevronLeft className={ICON} aria-hidden /> 이전
              </button>
              <span className="tabular">
                {page} / {pageCount}
              </span>
              <button
                disabled={page >= pageCount}
                onClick={() => {
                  setOffset((o) => o + pageSize);
                  setSelected([]);
                }}
                className={btnSecondary}
              >
                다음 <ChevronRight className={ICON} aria-hidden />
              </button>
            </div>
          </div>
        </>
      )}

      {dialog === "delete" && (
        <ConfirmDialog title={`진단 ${selected.length}건을 삭제할까요?`} confirmLabel="삭제" danger withReason busy={busy} onConfirm={bulk} onCancel={() => setDialog(null)}>
          <p>목록에서 숨겨지고 시작할 수 없게 됩니다. 증적, 발견 사항, 감사 기록은 그대로 보존되며 ‘삭제된 진단 보기’에서 복원할 수 있습니다.</p>
          <p className="mt-2 text-xs">실행 중인 진단은 건너뜁니다. 먼저 취소하세요.</p>
          <ul className="mt-2 max-h-24 overflow-y-auto font-mono text-xs text-faint">
            {selectedRuns.map((r) => (
              <li key={r.id}>{r.run_code}</li>
            ))}
          </ul>
        </ConfirmDialog>
      )}
      {dialog === "restore" && (
        <ConfirmDialog title={`진단 ${selected.length}건을 복원할까요?`} confirmLabel="복원" busy={busy} onConfirm={bulk} onCancel={() => setDialog(null)}>
          복원하면 다시 진단 목록에 나타나고 재개할 수 있습니다.
        </ConfirmDialog>
      )}
    </main>
  );
}

export default function RunsPage() {
  return (
    <AuthGate>
      <Header />
      <RunsTable />
    </AuthGate>
  );
}
