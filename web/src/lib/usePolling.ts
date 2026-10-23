"use client";

import { useEffect, useRef } from "react";

/**
 * 주기적으로 fn 을 호출한다. 탭이 숨겨져 있으면 멈추고, 다시 보이면 즉시 한 번 호출한 뒤 재개한다.
 * fn 은 최신 클로저를 쓰되 interval 이 재설정되지 않도록 ref 로 들고 있는다.
 */
export function usePolling(fn: () => void, ms: number, enabled = true) {
  const ref = useRef(fn);
  useEffect(() => {
    ref.current = fn;
  });

  useEffect(() => {
    if (!enabled) return;
    let id: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (id === null) id = setInterval(() => ref.current(), ms);
    };
    const stop = () => {
      if (id !== null) {
        clearInterval(id);
        id = null;
      }
    };
    const onVisibility = () => {
      if (document.hidden) {
        stop();
      } else {
        ref.current();
        start();
      }
    };
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [ms, enabled]);
}
