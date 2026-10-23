"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

// 버튼·입력 스타일은 한 곳에서만 정의해 화면마다 어긋나지 않게 한다.
const BTN_BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-40";
export const btnPrimary = `${BTN_BASE} bg-accent text-accent-ink hover:brightness-110`;
export const btnSecondary = `${BTN_BASE} border border-line bg-surface text-ink hover:bg-raised`;
export const btnDanger = `${BTN_BASE} border border-danger/50 text-danger hover:bg-danger/10`;
export const btnGhost = `${BTN_BASE} text-muted hover:bg-raised hover:text-ink`;
export const inputCls =
  "w-full rounded-md border border-line bg-canvas px-3 py-2 text-sm text-ink placeholder:text-faint focus:border-accent";

export function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`rounded-lg border border-line bg-surface ${className}`}>{children}</div>;
}

export function Banner({
  tone = "danger",
  children,
  onClose,
}: {
  tone?: "danger" | "warn" | "info";
  children: React.ReactNode;
  onClose?: () => void;
}) {
  const cls = {
    danger: "border-danger/40 bg-danger/10 text-danger",
    warn: "border-warn/40 bg-warn/10 text-warn",
    info: "border-info/40 bg-info/10 text-info",
  }[tone];
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={`mb-3 flex items-start justify-between gap-3 rounded-md border px-3 py-2 text-sm ${cls}`}
    >
      <div>{children}</div>
      {onClose && (
        <button onClick={onClose} className="shrink-0 underline underline-offset-2">
          닫기
        </button>
      )}
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-dashed border-line px-6 py-10 text-center">
      <p className="text-sm font-medium text-muted">{title}</p>
      {hint && <p className="mt-1 text-xs text-faint">{hint}</p>}
    </div>
  );
}

export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="불러오는 중">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-9 animate-pulse rounded-md bg-raised" />
      ))}
    </div>
  );
}

/**
 * 되돌리기 어려운 변경 앞의 확인 창. 부모가 `{open && <ConfirmDialog …/>}` 로 렌더하면 열 때마다 새로 마운트돼
 * 입력한 사유가 남지 않는다. 네이티브 <dialog> 라서 Esc 닫기·포커스 가두기가 기본으로 된다.
 */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  danger = false,
  withReason = false,
  busy = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  children: React.ReactNode;
  confirmLabel: string;
  danger?: boolean;
  withReason?: boolean;
  busy?: boolean;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [reason, setReason] = useState("");
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        onCancel();
      }}
      className="m-auto w-full max-w-md rounded-lg border border-line bg-surface p-0 text-ink backdrop:bg-black/60"
    >
      <div className="space-y-3 p-5">
        <h2 className="text-base font-semibold">{title}</h2>
        <div className="text-sm text-muted">{children}</div>
        {withReason && (
          <div>
            <label htmlFor="confirm-reason" className="mb-1 block text-xs text-muted">
              사유 (필수)
            </label>
            <textarea id="confirm-reason" rows={2} className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
          </div>
        )}
      </div>
      <div className="flex justify-end gap-2 border-t border-line px-5 py-3">
        <button onClick={onCancel} className={btnSecondary}>
          취소
        </button>
        <button
          disabled={busy || (withReason && !reason.trim())}
          onClick={() => onConfirm(reason.trim())}
          className={danger ? btnDanger : btnPrimary}
        >
          {confirmLabel}
        </button>
      </div>
    </dialog>
  );
}

// ── 토스트 ──
type Toast = { id: number; tone: "ok" | "danger"; text: string };
const ToastCtx = createContext<(tone: Toast["tone"], text: string) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((tone: Toast["tone"], text: string) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, tone, text }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 space-y-2" aria-live="polite">
        {items.map((t) => (
          <div
            key={t.id}
            className={`panel-in pointer-events-auto rounded-md border px-3 py-2 text-sm shadow-lg shadow-black/30 ${
              t.tone === "ok" ? "border-ok/40 bg-surface text-ok" : "border-danger/40 bg-surface text-danger"
            }`}
          >
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
