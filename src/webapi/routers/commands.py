"""명령(도구 호출) 이력: 모든 진단을 가로지르는 조회 전용 화면. 새 진단 로직 없음 — tool_invocations 를
traces.py 로 검색만 한다(진단 상세의 '실행 기록' 탭과 같은 테이블, 범위만 전체 진단으로 넓힘)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from diagnostic_workbench import traces as wb_traces

from ..deps import current_user, get_conn

router = APIRouter(prefix="/api/commands", tags=["commands"])


@router.get("")
def list_commands(q: str = "", tool: str | None = None, status: str | None = None, limit: int = 50,
                  offset: int = 0, conn=Depends(get_conn), user: str = Depends(current_user)):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    rows = wb_traces.search_all_worker_traces(conn, q, tool=tool, status=status, limit=limit, offset=offset)
    total = wb_traces.count_all_worker_traces(conn, q, tool=tool, status=status)
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/stats")
def command_stats(conn=Depends(get_conn), user: str = Depends(current_user)):
    return wb_traces.tool_stats(conn)
