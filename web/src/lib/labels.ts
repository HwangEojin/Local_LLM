// 화면에 보이는 용어는 한국어로 통일한다(API 값은 그대로 두고 표시만 바꿈). 모르는 값은 원문을 그대로 보여 준다.

export type Tone = "danger" | "warn" | "ok" | "info" | "neutral";

type Entry = { label: string; tone: Tone };

const MAP: Record<string, Entry> = {
  // Finding 상태
  CANDIDATE: { label: "후보", tone: "warn" },
  NEEDS_REVIEW: { label: "검토 필요", tone: "warn" },
  CONFIRMED: { label: "확정", tone: "danger" },
  REJECTED: { label: "기각", tone: "neutral" },
  INCONCLUSIVE: { label: "결론 불가", tone: "neutral" },
  NOT_TESTED: { label: "미검증", tone: "neutral" },
  // Run 상태
  CREATED: { label: "생성됨", tone: "neutral" },
  RUNNING: { label: "실행 중", tone: "info" },
  PAUSED: { label: "일시정지", tone: "warn" },
  COMPLETED: { label: "완료", tone: "ok" },
  CANCELLED: { label: "취소됨", tone: "neutral" },
  // 작업 큐 / 도구 호출 상태
  PENDING: { label: "대기", tone: "neutral" },
  READY: { label: "준비됨", tone: "info" },
  BLOCKED: { label: "차단됨", tone: "warn" },
  SUCCEEDED: { label: "성공", tone: "ok" },
  FAILED: { label: "실패", tone: "danger" },
  // 심각도
  critical: { label: "치명", tone: "danger" },
  high: { label: "높음", tone: "danger" },
  medium: { label: "중간", tone: "warn" },
  low: { label: "낮음", tone: "neutral" },
  info: { label: "정보", tone: "info" },
};

export function describe(value: string): Entry {
  return MAP[value] ?? { label: value, tone: "neutral" };
}

export const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"] as const;

export const AUTONOMY_LABEL: Record<string, string> = {
  approval: "항상 승인",
  full: "범위 내 자동",
};

export const VERIFICATION_LABEL: Record<string, string> = {
  LLM_CONFIRMED: "AI 확정",
  HUMAN: "사람 확인",
  RULE: "규칙 확정",
};

export const DOCTOR_LABEL: Record<string, string> = {
  OK: "정상",
  WARN: "주의",
  FAIL: "오류",
  INFO: "참고",
};

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  // YYYY.MM.DD HH:mm:ss (브라우저 로컬 시간대)
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}.${p(d.getMonth() + 1)}.${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function formatBytes(n: number | null | undefined): string {
  if (n == null) return "-";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// 에이전트 역할 -> 화면 표시 이름 (서버의 explore.AGENT_ROLES 와 같은 키)
export const AGENT_ROLE_LABEL: Record<string, string> = {
  planner: "계획(Planner)",
  worker: "실행 도구 선택(Worker)",
  analyst: "체크리스트 분석",
  goals: "목표 분해",
  reporter: "보고서 문장",
};
