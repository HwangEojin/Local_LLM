// 설정 화면의 구역들이 같이 쓰는 작은 부품(page.tsx 와 구역별 파일이 함께 import 한다).
import { Lock } from "lucide-react";

import { inputCls } from "@/components/ui";
import { ApiError } from "@/lib/api";

export const ICON = "h-4 w-4 shrink-0";

export const errMsg = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

export function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-faint">{hint}</span>}
    </label>
  );
}

export function NumField({ label, hint, value, onChange, min = 0 }: { label: string; hint?: string; value: number; onChange: (n: number) => void; min?: number }) {
  return (
    <Field label={label} hint={hint}>
      <input type="number" min={min} value={Number.isFinite(value) ? value : ""} onChange={(e) => onChange(e.target.valueAsNumber)} className={inputCls} />
    </Field>
  );
}

export function ReadOnlyRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-line bg-canvas px-3 py-2 text-sm">
      <span className="flex items-center gap-2 text-muted">
        <Lock className="h-3.5 w-3.5" aria-hidden />
        {label}
      </span>
      <span className="font-mono text-xs text-ink">{value || "(없음)"}</span>
    </div>
  );
}
