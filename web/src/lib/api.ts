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
      const d = body.detail ?? detail;
      // FastAPI 검증 오류는 [{loc, msg}] 배열로 온다 — 사람이 읽는 문장으로 바꾼다
      detail = Array.isArray(d) ? d.map((x: { msg?: string }) => x.msg ?? JSON.stringify(x)).join("; ") : String(d);
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
  put: <T>(path: string, body: unknown) => request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
};

export const downloadUrl = (runId: string, evidenceId: string) =>
  `${API_BASE}/api/runs/${runId}/evidence/${evidenceId}/download`;

export type Run = {
  id: string;
  run_code: string; // 사람이 읽는 진단 ID. 예: BASE-20261023-01
  deleted_at: string | null; // 소프트 삭제 표시(행·증적·감사 기록은 남아 있고 복원 가능)
  delete_reason: string | null;
  status: string;
  operator: string;
  autonomy: string;
  use_llm: boolean;
  llm_provider: string;
  created_at: string;
  stop_reason: string | null;
  has_checklist: boolean;
  findings_count: number;
};

export type RunList = { runs: Run[]; total: number; limit: number; offset: number };

export type BulkDeleteResult = { deleted: string[]; skipped: { ref: string; reason: string }[] };

export const RUN_STATUSES = ["CREATED", "RUNNING", "PAUSED", "COMPLETED", "CANCELLED", "FAILED"] as const;

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

export type FindingDetail = {
  finding: Finding;
  evidence: {
    id: string;
    tool: string;
    success: boolean;
    size: number | null;
    media_type: string | null;
    collected_at: string;
  }[];
  history: {
    old_status: string | null;
    new_status: string;
    reason: string;
    actor: string;
    evidence_ids: string[];
    changed_at: string;
  }[];
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
  targets: string[];
  coverage: { verdicts: Record<string, number>; backlog: Record<string, number> };
  usage: { tools: number; explore_tools: number; evidence: number; findings: number; llm_calls: number };
  loop_running: boolean;
};

export type Evidence = {
  id: string;
  tool: string;
  tool_version: string;
  requirement_id: string;
  sha256: string | null;
  size: number | null;
  truncated: boolean;
  success: boolean;
  error: string | null;
  media_type: string | null;
  collected_at: string;
  integrity: string | null;
  integrity_detail: string | null;
};

export type ActivityEntry = {
  id: string;
  run_id: string;
  backlog_item_id: string;
  step_no: number;
  tool: string;
  tool_version: string;
  args: unknown;
  policy_decision: string;
  policy_reason: string | null;
  status: string;
  error: string | null;
  evidence_id: string | null;
  started_at: string;
  ended_at: string | null;
  asset_kind: string | null;
  asset_value: string | null;
  task_kind: string | null;
  task_reason: string | null;
  evidence_media_type: string | null;
  evidence_size: number | null;
  evidence_success: boolean | null;
};

export type ServerLogEntry = { ts: string; message: string };

export type TrafficEntry = {
  id: string;
  tool: "proxy.http" | "proxy.https";
  args: { method?: string; url?: string };
  collected_at: string;
  success: boolean;
  size: number | null;
  media_type: string | null;
};

export type Question = {
  id: string;
  state: "OPEN" | "ANSWERED";
  payload: { text?: string; answer?: string; answered_by?: string; [k: string]: unknown };
  created_at: string;
};

export type DashboardData = {
  by_status: { status: string; n: number }[];
  by_severity: { severity: string; n: number }[];
  totals: { runs: number; evidence: number; findings: number; assets: number };
  recent_runs: { id: string; run_code: string; status: string; operator: string; created_at: string; findings_count: number }[];
};

export type Target = {
  target_id: string;
  type: string;
  value: string;
  platform?: string;
  description?: string;
  code?: string; // 진단 ID 의 대상 약어(선택)
};

export type Scope = {
  engagement_id: string;
  approved_by: string;
  valid_until: string;
  allowed_risks: string[];
  targets: Target[];
};

export type WorkbenchSettings = {
  exclude: string[];
  lease_s: number;
  max_attempts: number;
  approval_ttl_h: number;
  min_call_interval_ms: number;
  budget: {
    max_tasks: number;
    max_tool_calls: number;
    timeout_s: number;
    max_explorations: number;
    max_tool_calls_exploration: number;
  };
  nmap_path: string | null;
  testssl_path: string | null;
};

export type LlmSettings = {
  ollama: {
    base_url: string;
    model: string;
    timeout_s: number;
    max_request_bytes: number;
    max_response_bytes: number;
    max_calls_per_run: number;
    max_retries: number;
  };
  limits: { max_tool_calls_per_run: number; run_timeout_s: number; max_evidence_bytes: number };
  external_api: { enabled: boolean };
  paths: Record<string, string>;
};

export type KillSwitch = { active: boolean; by?: string; at?: string; reason?: string };

export type McpServer = {
  name: string;
  enabled: boolean;
  description: string;
  transport: string;
  timeout_s: number;
  tools: string[];
  source: "shared" | "local";
};

export type ChecklistEntry = { checklist_id: string; version: string; status: string; sha256: string | null };

export type AuditEvent = {
  id: number;
  ts: string;
  event: string;
  details: Record<string, unknown>;
  run_code: string | null;
};

export type ChecklistRef = { checklist_id: string; version: string; sha256: string | null };

export type DoctorRow = { status: "OK" | "WARN" | "FAIL" | "INFO"; name: string; detail: string };

export type Environment = {
  targets: Target[];
  checklists: ChecklistRef[];
  tools: { name: string; risk: string }[];
  autonomy_levels: string[];
  llm_providers: string[];
  doctor: DoctorRow[];
};

export const FINDING_STATUSES = [
  "CANDIDATE",
  "NEEDS_REVIEW",
  "CONFIRMED",
  "REJECTED",
  "INCONCLUSIVE",
  "NOT_TESTED",
] as const;
