"use client";

// 전역 자산 화면(G5-b, UPGRADE_PLAN.md) — 실행을 가로질러 자산별 관찰 실행 수·중복 제외 finding 수·최근 관찰
// 시각을 본다. 새 진단 로직 없음: /api/assets(global_views.py, 기존 assets/run_assets/evidence/findings
// 조인)만 읽는다. 쓰기 동작 없음(읽기 전용 화면).
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";

import { Banner, btnGhost, Card, Empty, inputCls, SkeletonRows } from "@/components/ui";
import { api, GlobalAsset } from "@/lib/api";
import { ASSET_VERIFICATION_LABEL, formatTime } from "@/lib/labels";
import { usePolling } from "@/lib/usePolling";

const PAGE = 50;
const th = "px-3 py-1.5 text-xs font-medium";

export default function AssetsPage() {
  const [items, setItems] = useState<GlobalAsset[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [offset, setOffset] = useState(0);

  const load = useCallback(() => {
    const p = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
    if (q.trim()) p.set("q", q.trim());
    if (kind.trim()) p.set("kind", kind.trim());
    api
      .get<{ items: GlobalAsset[]; total: number }>(`/api/assets?${p.toString()}`)
      .then((r) => { setItems(r.items); setTotal(r.total); setError(null); })
      .catch((e) => setError(e.message));
  }, [q, kind, offset]);
  useEffect(load, [load]);
  usePolling(load, 20000);

  const filtering = q !== "" || kind !== "";
  const page = Math.floor(offset / PAGE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const kinds = useMemo(() => Array.from(new Set((items ?? []).map((a) => a.kind))).sort(), [items]);

  return (
    <main className="mx-auto w-full max-w-6xl space-y-4 p-6">
      <h1 className="text-lg font-semibold">자산</h1>
      <p className="text-sm text-muted">모든 진단에서 관찰된 자산을 가로질러 검색합니다.</p>
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" aria-hidden />
          <input
            value={q}
            onChange={(e) => { setQ(e.target.value); setOffset(0); }}
            placeholder="값 검색(호스트·URL 등)"
            aria-label="자산 검색"
            className={`${inputCls} !w-60 pl-8`}
          />
        </div>
        {kinds.length > 0 && (
          <select value={kind} onChange={(e) => { setKind(e.target.value); setOffset(0); }} className={inputCls}>
            <option value="">모든 종류</option>
            {kinds.map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
        )}
        {filtering && (
          <button onClick={() => { setQ(""); setKind(""); setOffset(0); }} className={btnGhost}>
            필터 초기화
          </button>
        )}
        <span className="ml-auto text-xs text-faint tabular">전체 {total}건</span>
      </div>

      {!items ? (
        <SkeletonRows rows={6} />
      ) : items.length === 0 ? (
        <Empty title="조건에 맞는 자산이 없습니다" hint={filtering ? "필터를 줄여 보세요." : "진단이 진행되면 관찰된 자산이 여기에 쌓입니다."} />
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-raised text-left text-muted">
              <tr>
                <th scope="col" className={th}>종류</th>
                <th scope="col" className={th}>값</th>
                <th scope="col" className={th}>확인 상태</th>
                <th scope="col" className={th}>관찰 실행 수</th>
                <th scope="col" className={th}>발견 사항(중복 제외)</th>
                <th scope="col" className={th}>최근 관찰</th>
              </tr>
            </thead>
            <tbody>
              {items.map((a) => (
                <tr key={a.id} className="border-t border-line transition-colors hover:bg-raised/60">
                  <td className="px-3 py-1.5 font-mono text-xs text-muted">{a.kind}</td>
                  <td className="px-3 py-1.5 font-medium">
                    <Link href={`/assets/${a.id}`} className="text-accent hover:underline">
                      {a.value}
                    </Link>
                    {a.company && <span className="ml-2 text-xs text-faint">{a.company}</span>}
                  </td>
                  <td className="px-3 py-1.5 text-muted">{ASSET_VERIFICATION_LABEL[a.verification] ?? a.verification}</td>
                  <td className="px-3 py-1.5 tabular text-muted">{a.observed_runs}</td>
                  <td className="px-3 py-1.5 tabular text-muted">{a.finding_count}</td>
                  <td className="px-3 py-1.5 text-muted">{a.last_observed_at ? formatTime(a.last_observed_at) : "없음"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {pages > 1 && (
        <div className="flex items-center justify-center gap-3 text-sm text-muted">
          <button disabled={page <= 1} onClick={() => setOffset(Math.max(0, offset - PAGE))} className={btnGhost}>
            <ChevronLeft className="h-4 w-4" aria-hidden /> 이전
          </button>
          <span className="tabular">{page} / {pages}</span>
          <button disabled={page >= pages} onClick={() => setOffset(offset + PAGE)} className={btnGhost}>
            다음 <ChevronRight className="h-4 w-4" aria-hidden />
          </button>
        </div>
      )}
    </main>
  );
}
