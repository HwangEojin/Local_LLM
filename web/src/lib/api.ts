const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8420";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // 본문이 JSON 이 아니면 상태 텍스트만 쓴다
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
};

export type Run = {
  id: string;
  status: string;
  operator: string;
  autonomy: string;
  use_llm: boolean;
  created_at: string;
  stop_reason: string | null;
  has_checklist: boolean;
  findings_count: number;
};

export type Finding = {
  id: string;
  run_id: string;
  asset_id: string;
  title: string;
  severity: string;
  status: string;
  rationale: string;
  remediation: string;
  verification: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_reason: string | null;
  created_at: string;
};

export type BacklogItem = {
  id: string;
  kind: string;
  status: string;
  reason: string;
  policy_class: string | null;
  blocked_reason: string | null;
  attempts: number;
  created_at: string;
  updated_at: string;
};

export type RunDetail = {
  run: Run & { id: string; [k: string]: unknown };
  coverage: { verdicts: Record<string, number>; backlog: Record<string, number> };
  usage: { tools: number; explore_tools: number; evidence: number; findings: number; llm_calls: number };
};

export const FINDING_STATUSES = [
  "CANDIDATE",
  "NEEDS_REVIEW",
  "CONFIRMED",
  "REJECTED",
  "INCONCLUSIVE",
  "NOT_TESTED",
] as const;
