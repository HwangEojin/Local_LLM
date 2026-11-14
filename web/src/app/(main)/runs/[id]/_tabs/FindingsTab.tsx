"use client";

import { useMemo } from "react";
import { Download } from "lucide-react";

import { StatusBadge } from "@/components/StatusBadge";
import { btnGhost, Card, Empty, inputCls, SkeletonRows } from "@/components/ui";
import { Finding, FINDING_STATUSES, findingsExportUrl } from "@/lib/api";
import { describe, formatTime, VERIFICATION_LABEL } from "@/lib/labels";
import { chip, FindingFilter, NO_FILTER, th } from "../_shared";

// ── 발견 사항 ──
export function FindingsTab({
  runId,
  findings,
  filter,
  setFilter,
  selectedId,
  onSelect,
}: {
  runId: string;
  findings: Finding[] | null;
  filter: FindingFilter;
  setFilter: (f: FindingFilter) => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const toggle = (key: "statuses", v: string) =>
    setFilter({ ...filter, [key]: filter[key].includes(v) ? filter[key].filter((x) => x !== v) : [...filter[key], v] });

  const rows = useMemo(() => {
    if (!findings) return null;
    const q = filter.q.trim().toLowerCase();
    return findings
      .filter((f) => !filter.statuses.length || filter.statuses.includes(f.status))
      .filter((f) => !q || f.title.toLowerCase().includes(q) || f.rationale.toLowerCase().includes(q))
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [findings, filter]);

  if (!findings) return <SkeletonRows />;
  if (findings.length === 0) return <Empty title="아직 발견 사항이 없습니다" hint="진단이 진행되면 취약점 후보가 여기에 쌓입니다." />;

  const filtering = filter.statuses.length > 0 || filter.q !== "";

  return (
    <div>
      <div className="mb-3 space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <input
            value={filter.q}
            onChange={(e) => setFilter({ ...filter, q: e.target.value })}
            placeholder="제목·근거 검색"
            aria-label="발견 사항 검색"
            className={`${inputCls} !w-60`}
          />
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="판정 상태 필터">
            {FINDING_STATUSES.map((s) => (
              <button key={s} aria-pressed={filter.statuses.includes(s)} onClick={() => toggle("statuses", s)} className={chip(filter.statuses.includes(s))}>
                {describe(s).label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {filtering && (
            <button onClick={() => setFilter(NO_FILTER)} className={btnGhost}>
              필터 초기화
            </button>
          )}
          <a href={findingsExportUrl(runId, "csv")} className={btnGhost} download>
            <Download className="h-4 w-4" aria-hidden /> CSV
          </a>
          <a href={findingsExportUrl(runId, "json")} className={btnGhost} download>
            <Download className="h-4 w-4" aria-hidden /> JSON
          </a>
          <span className="ml-auto text-xs text-faint tabular">
            {rows?.length ?? 0} / {findings.length}건
          </span>
        </div>
      </div>

      {rows && rows.length === 0 ? (
        <Empty title="조건에 맞는 발견 사항이 없습니다" hint="필터를 줄여 보세요." />
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-raised text-left text-muted">
              <tr>
                <th scope="col" className={th}>제목</th>
                <th scope="col" className={th}>판정</th>
                <th scope="col" className={th}>검증 방식</th>
                <th scope="col" className={th}>검토자</th>
                <th scope="col" className={th}>발견 시각</th>
              </tr>
            </thead>
            <tbody>
              {rows?.map((f) => (
                <tr
                  key={f.id}
                  tabIndex={0}
                  aria-selected={selectedId === f.id}
                  onClick={() => onSelect(f.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(f.id);
                    }
                  }}
                  className={`cursor-pointer border-t border-line transition-colors hover:bg-raised/60 ${selectedId === f.id ? "bg-raised" : ""}`}
                >
                  <td className="px-4 py-2 font-medium">
                    {f.title}
                    {!!f.suspected_duplicates && (
                      <span className="ml-2 rounded-full bg-warn/15 px-2 py-0.5 text-xs font-medium text-warn">
                        ⚠ 중복 의심 {f.suspected_duplicates}건
                      </span>
                    )}
                    {!!f.merged_count && (
                      <span className="ml-2 rounded-full bg-raised px-2 py-0.5 text-xs font-medium text-muted">
                        중복 {f.merged_count}건 병합됨
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2"><StatusBadge value={f.status} /></td>
                  <td className="px-4 py-2 text-muted">{VERIFICATION_LABEL[f.verification] ?? f.verification}</td>
                  <td className="px-4 py-2 text-muted">{f.reviewed_by ?? "검토 전"}</td>
                  <td className="px-4 py-2 text-muted">{formatTime(f.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
