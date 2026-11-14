// G4(UPGRADE_PLAN.md): 탭/부분 컴포넌트가 공유하는 타입·상수·훅. 순수 이동(동작 변경 없음).
import { BacklogItem } from "@/lib/api";
import { useRunResource } from "@/lib/useRunResource";

export type TabKey = "findings" | "backlog" | "assets" | "graph" | "traffic" | "chat" | "evidence" | "activity" | "server-log" | "triggers" | "report";
export const TAB_KEYS: TabKey[] = ["findings", "backlog", "assets", "graph", "traffic", "chat", "evidence", "activity", "server-log", "triggers", "report"];
export const TAB_LABEL: Record<TabKey, string> = {
  findings: "발견 사항",
  backlog: "작업 큐",
  assets: "자산",
  graph: "탐색 그래프",
  traffic: "트래픽",
  chat: "질문",
  evidence: "증적",
  activity: "실행 기록",
  "server-log": "서버 로그",
  triggers: "트리거",
  report: "보고서",
};

export type FindingFilter = { statuses: string[]; q: string };
export const NO_FILTER: FindingFilter = { statuses: [], q: "" };
export const REVIEW_PENDING = ["CANDIDATE", "NEEDS_REVIEW"];

export const needsApproval = (it: BacklogItem) => it.status === "BLOCKED" && it.policy_class === "ALLOW_WITH_APPROVAL";

export const chip = (on: boolean) =>
  `rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
    on ? "border-accent bg-accent/15 text-accent" : "border-line text-muted hover:border-faint hover:text-ink"
  }`;

export const th = "px-4 py-2 font-medium";

// ── 자산(여러 탭이 공유) ──
export type AssetRow = { id: string; target_id: string; kind: string; value: string; company: string | null; verification: string; source: string };
export type AssetEdgeRow = { src: string; rel: string; dst: string; source: string };
export type AssetsData = { assets: AssetRow[]; edges: AssetEdgeRow[] };
export const useAssets = (runId: string) => useRunResource<AssetsData>(`/api/runs/${runId}/assets`, 6000);

// ── 트리거(여러 곳에서 공유) ──
export const TRIGGER_KIND_LABEL: Record<string, string> = {
  interval: "일정 간격마다",
  on_finding: "새 발견 사항이 생기면",
  on_goal_met: "체크리스트 전부 끝나면",
  on_task_timeout: "도구 호출이 시간 초과로 실패하면",
  on_tool_call: "특정 도구가 성공하면",
  on_task_create: "새 작업이 생기면",
};

export function parseJsonOr(text: string, fallback: Record<string, unknown>): Record<string, unknown> | null {
  if (!text.trim()) return fallback;
  try {
    const v = JSON.parse(text);
    return typeof v === "object" && v !== null ? v : null;
  } catch {
    return null;
  }
}
