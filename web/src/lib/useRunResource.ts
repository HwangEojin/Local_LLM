"use client";

import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "./api";
import { usePolling } from "./usePolling";

/**
 * 런 상세 페이지의 탭들이 전부 반복하던 fetch+polling+error 상태 관리를 한 곳으로 모은다
 * (findings/backlog/assets/graph/traffic/chat/evidence/activity/server-log 탭이 전부 같은 모양이었음).
 */
export function useRunResource<T>(path: string, pollMs: number, enabled = true) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    api
      .get<T>(path)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [path]);

  useEffect(reload, [reload]);
  // pollMs=0 이면 폴링 없이 마운트 시 한 번만 불러온다(증적 탭처럼 자동 새로고침이 없던 탭도 그대로 지원).
  usePolling(reload, pollMs, enabled && pollMs > 0);

  return { data, error, reload };
}
