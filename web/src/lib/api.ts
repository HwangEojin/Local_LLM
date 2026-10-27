// 백엔드 호스트를 페이지 자신의 호스트명에 맞춘다(고정값이 아님). 프론트(localhost:3000)와
// 백엔드(127.0.0.1:8420)의 호스트명이 다르면 브라우저가 둘을 다른 사이트로 봐서 로그인 세션
// 쿠키(SameSite=Lax)를 다음 요청에 안 실어 보낸다 — 로그인 POST 는 성공하는데 세션은 안 먹히는
// 현상의 원인. 항상 "지금 열려 있는 호스트명 그대로" 백엔드를 불러서 같은 사이트로 맞춘다.
function apiBase(): string {
  if (process.env.NEXT_PUBLIC_API_BASE) return process.env.NEXT_PUBLIC_API_BASE;
  if (typeof window !== "undefined") return `http://${window.location.hostname}:8420`;
  return "http://127.0.0.1:8420"; // SSR 등 window 가 없을 때의 대비값(실제로는 안 쓰임 — 전부 "use client")
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
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
  `${apiBase()}/api/runs/${runId}/evidence/${evidenceId}/download`;

export const findingsExportUrl = (runId: string, format: "csv" | "json") =>
  `${apiBase()}/api/runs/${runId}/findings/export?format=${format}`;

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
  // 목표 노드. 2개 이상이면 운영자가 쓴 목표가 대상별 하위 목표로 분해된 것(멀티에이전트 2단계)
  goals: { text: string; targets: string[] }[];
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
  llm_profiles: { name: string; kind: string; model: string | null }[];
  ollama_models: string[] | null; // null = Ollama 에 연결할 수 없음
  agent_roles: string[];
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

export type LlmProfileRef = { name: string; kind: string; model: string | null };

// 모델 연결 테스트(서버가 실제 check_ready 를 호출). spec: 'ollama:<모델>' | 프로파일 이름 | null(provider 기본 모델)
export async function testLlm(spec: string | null, provider = "ollama"): Promise<{ ok: boolean; text: string }> {
  try {
    const r = await api.post<{ ok: boolean; model?: string; version?: string; error?: string; reply?: unknown; elapsed_ms?: number }>(
      "/api/environment/llm-test",
      { spec, provider },
    );
    return r.ok
      ? {
          ok: true,
          text: `연결됨 · ${r.model}${r.version ? ` (Ollama v${r.version})` : ""} · 실제 응답 ${JSON.stringify(r.reply)} (${r.elapsed_ms}ms)`,
        }
      : { ok: false, text: r.error ?? "연결하지 못했습니다" };
  } catch (e) {
    return { ok: false, text: e instanceof Error ? e.message : "테스트 실패" };
  }
}

export type AgentPrompt = {
  name: string;
  label: string;
  default: string;
  text: string;
  overridden: boolean;
  updated_by: string | null;
  updated_at: string | null;
};
export type AgentRoleInfo = { role: string; prompts: AgentPrompt[] };

export type ToolInfo = {
  name: string;
  risk: string; // 실제 적용 위험도(오버라이드 반영)
  base_risk: string; // 원래 등록된 위험도
  disabled: boolean;
  args_schema: Record<string, unknown>;
  scope_args: Record<string, string>;
  source: "mcp" | "builtin";
  allowed_by_scope: boolean;
};
export type ToolCatalog = { allowed_risks: string[]; tools: ToolInfo[] };

export type ApprovalsOverview = {
  ttl_h: number;
  pending: {
    id: string;
    run_code: string;
    kind: string;
    reason: string;
    tool: string | null;
    updated_at: string;
    asset_kind: string;
    asset_value: string;
  }[];
  recent: {
    id: string;
    run_code: string;
    tool: string;
    decision: "APPROVED" | "REJECTED";
    approver: string;
    decided_at: string;
    expires_at: string;
    expired: boolean;
    asset_value: string;
  }[];
};

export type NotificationChannel = {
  name: string;
  kind: string;
  enabled: boolean;
  source: "shared" | "local";
  url_masked: string;
  external: boolean;
};

export type Skill = {
  name: string;
  description: string;
  license: string;
  source: "manual" | "import";
  instructions: string;
  roles: string[];
  enabled: boolean;
  updated_by: string;
  updated_at: string;
};
export type SkillUsage = {
  recent: { run_id: string; run_code: string | null; role: string; calls: number; first_at: string; last_at: string }[];
  by_role: { role: string; runs: number; calls: number }[];
};
export type SkillsResponse = { roles: string[]; skills: Skill[] };

export type DiscoveredTool = {
  name: string;
  description: string;
  input_schema: { properties?: Record<string, { type?: string; description?: string }> } & Record<string, unknown>;
  schema_ok: boolean;
  schema_error: string | null;
  suggested_scope: Record<string, "path" | "host" | "url">;
};
export type McpDiscovery = { ok: true; server: string; tools: DiscoveredTool[] } | { ok: false; error: string };

export type InterceptRule = {
  name: string;
  tool_pattern: string;
  arg_pattern?: string;
  enabled: boolean;
  source: "shared" | "local";
};

export const auditExportUrl = (event: string, run: string, format: "csv" | "json") => {
  const p = new URLSearchParams({ format });
  if (event.trim()) p.set("event", event.trim());
  if (run.trim()) p.set("run", run.trim());
  return `${apiBase()}/api/admin/audit/export?${p.toString()}`;
};
