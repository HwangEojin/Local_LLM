// 도구 구역: 등록된 도구 카탈로그(조회 전용). 도구 등록·삭제는 웹에서 하지 않는다 — MCP 서버 활성화는 MCP 구역,
// 실행 파일 경로는 workbench.json, 실행 가능 여부는 매 호출 서버의 정책 검사가 정한다.
import { useEffect, useMemo, useState } from "react";

import { Banner, Card, inputCls, SkeletonRows } from "@/components/ui";
import { api, ToolCatalog } from "@/lib/api";

const RISK_CLS: Record<string, string> = {
  read: "bg-ok/15 text-ok",
  write: "bg-warn/15 text-warn",
  dangerous: "bg-danger/15 text-danger",
};

export function ToolsSection() {
  const [cat, setCat] = useState<ToolCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [risk, setRisk] = useState("");

  useEffect(() => {
    api.get<ToolCatalog>("/api/admin/tools").then(setCat).catch((e) => setError(e.message));
  }, []);

  const rows = useMemo(
    () => (cat?.tools ?? []).filter((t) => (!risk || t.risk === risk) && t.name.toLowerCase().includes(q.trim().toLowerCase())),
    [cat, q, risk],
  );

  if (error && !cat) return <Banner>{error}</Banner>;
  if (!cat) return <SkeletonRows rows={5} />;
  return (
    <Card className="space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <input className={`${inputCls} max-w-xs`} placeholder="도구 이름 검색" value={q} onChange={(e) => setQ(e.target.value)} aria-label="도구 이름 검색" />
        <select className={`${inputCls} max-w-[10rem]`} value={risk} onChange={(e) => setRisk(e.target.value)} aria-label="위험도 필터">
          <option value="">모든 위험도</option>
          <option value="read">read</option>
          <option value="write">write</option>
          <option value="dangerous">dangerous</option>
        </select>
        <span className="text-xs text-muted">
          {rows.length} / {cat.tools.length}개 · 현재 범위가 허용하는 위험도: {cat.allowed_risks.join(", ") || "없음"}
        </span>
      </div>
      <ul className="divide-y divide-line">
        {rows.map((t) => (
          <li key={t.name} className="py-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-sm">{t.name}</span>
              <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${RISK_CLS[t.risk] ?? "bg-raised text-muted"}`}>{t.risk}</span>
              <span className="text-xs text-faint">{t.source === "mcp" ? "MCP" : "내장"}</span>
              {!t.allowed_by_scope && <span className="text-xs text-warn">현재 범위가 허용하지 않는 위험도</span>}
            </div>
            <details className="mt-1">
              <summary className="cursor-pointer text-xs text-muted">인자 스키마 · 범위 검사 인자 {Object.keys(t.scope_args).length}개</summary>
              <pre className="mt-1 max-h-48 overflow-auto rounded bg-canvas p-2 text-xs">{JSON.stringify({ args_schema: t.args_schema, scope_args: t.scope_args }, null, 2)}</pre>
            </details>
          </li>
        ))}
        {rows.length === 0 && <li className="py-3 text-sm text-faint">조건에 맞는 도구가 없습니다.</li>}
      </ul>
      <p className="text-xs text-faint">
        도구 등록·삭제는 웹에서 하지 않습니다. MCP 서버는 MCP 구역에서 활성/비활성하고, nmap·testssl 경로는 config/workbench.json 에서 바꿉니다. 도구가 목록에 있어도 실행은 매 호출마다 범위·위험도·승인 검사를 통과해야 합니다.
      </p>
    </Card>
  );
}
