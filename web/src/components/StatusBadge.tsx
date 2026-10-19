const COLORS: Record<string, string> = {
  CANDIDATE: "bg-amber-100 text-amber-800",
  NEEDS_REVIEW: "bg-orange-100 text-orange-800",
  CONFIRMED: "bg-red-100 text-red-800",
  REJECTED: "bg-neutral-100 text-neutral-500",
  INCONCLUSIVE: "bg-neutral-100 text-neutral-600",
  NOT_TESTED: "bg-neutral-100 text-neutral-500",
  RUNNING: "bg-blue-100 text-blue-800",
  COMPLETED: "bg-green-100 text-green-800",
  PAUSED: "bg-amber-100 text-amber-800",
  CANCELLED: "bg-neutral-100 text-neutral-500",
  CREATED: "bg-neutral-100 text-neutral-600",
  READY: "bg-blue-100 text-blue-800",
  PENDING: "bg-neutral-100 text-neutral-600",
  BLOCKED: "bg-orange-100 text-orange-800",
  FAILED: "bg-red-100 text-red-800",
  SUCCEEDED: "bg-green-100 text-green-800",
  high: "bg-red-100 text-red-800",
  critical: "bg-red-200 text-red-900",
  medium: "bg-orange-100 text-orange-800",
  low: "bg-neutral-100 text-neutral-600",
};

export function StatusBadge({ value }: { value: string }) {
  const cls = COLORS[value] ?? "bg-neutral-100 text-neutral-600";
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>{value}</span>
  );
}
