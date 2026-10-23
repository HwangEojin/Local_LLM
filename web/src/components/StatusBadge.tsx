import { describe, Tone } from "@/lib/labels";

const TONE: Record<Tone, string> = {
  danger: "bg-danger/15 text-danger",
  warn: "bg-warn/15 text-warn",
  ok: "bg-ok/15 text-ok",
  info: "bg-info/15 text-info",
  neutral: "bg-raised text-muted",
};

const DOT: Record<Tone, string> = {
  danger: "bg-danger",
  warn: "bg-warn",
  ok: "bg-ok",
  info: "bg-info",
  neutral: "bg-faint",
};

// 색만으로 구분하지 않도록 점 + 한글 라벨을 함께 쓴다.
export function StatusBadge({ value }: { value: string }) {
  const { label, tone } = describe(value);
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${TONE[tone]}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${DOT[tone]}`} aria-hidden />
      {label}
    </span>
  );
}
