"""진단을 가로지르는 조회 전용 화면: 발견 사항/트래픽(UPGRADE_PLAN.md B2 — commands.py 와 같은 원칙).

새 진단 로직 없음 — 기존 테이블(findings/evidence)을 runs 와 조인해 전체 범위로 보여줄 뿐이다. 상세·검토·
재검증 같은 쓰기 동작은 여전히 run 범위 엔드포인트(/api/runs/{run_ref}/findings/{id} 등)를 그대로 쓴다
(각 행이 들고 있는 run_id 로 호출 — 새 쓰기 경로 없음)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import current_user, get_conn

router = APIRouter(tags=["findings", "traffic"])


@router.get("/api/findings")
def list_all_findings(q: str = "", status: str | None = None, limit: int = 100,
                      offset: int = 0, conn=Depends(get_conn), user: str = Depends(current_user)):
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    where, params = ["r.deleted_at IS NULL"], []
    if q.strip():
        where.append("(f.title ILIKE %s OR f.rationale ILIKE %s)")
        params += [f"%{q.strip()}%", f"%{q.strip()}%"]
    if status:
        where.append("f.status = %s")
        params.append(status)
    sql_where = " AND ".join(where)
    total = conn.execute(f"SELECT count(*) AS n FROM findings f JOIN runs r ON r.id = f.run_id WHERE {sql_where}",
                         params).fetchone()["n"]
    rows = conn.execute(
        f"SELECT f.*, r.run_code FROM findings f JOIN runs r ON r.id = f.run_id WHERE {sql_where} "
        "ORDER BY f.created_at DESC LIMIT %s OFFSET %s", params + [limit, offset]).fetchall()
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/api/llm-records")
def list_llm_records(run: str = "", purpose: str = "", limit: int = 50, offset: int = 0,
                     conn=Depends(get_conn), user: str = Depends(current_user)):
    """LLM 호출 원문 기록(레퍼런스 프로젝트 llmrec 참고, migrations/0020) 을 전체 진단 범위로 모은 목록. 새 진단 로직
    없음 — model_calls 를 runs 와 조인해 조회만 한다. request_text/response_text 는 저장 시점에 이미
    마스킹됐다(explore.py 의 _ModelLog.record_model_call)."""
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    where, params = ["r.deleted_at IS NULL"], []
    if run.strip():
        where.append("r.run_code ILIKE %s")
        params.append(f"%{run.strip()}%")
    if purpose.strip():
        where.append("mc.purpose = %s")
        params.append(purpose.strip())
    sql_where = " AND ".join(where)
    total = conn.execute(f"SELECT count(*) AS n FROM model_calls mc JOIN runs r ON r.id = mc.run_id "
                         f"WHERE {sql_where}", params).fetchone()["n"]
    rows = conn.execute(
        "SELECT mc.id, mc.ts, mc.model, mc.purpose, mc.prompt_tokens, mc.completion_tokens, mc.duration_ms, "
        "mc.ok, mc.error, mc.request_text, mc.response_text, r.run_code "
        f"FROM model_calls mc JOIN runs r ON r.id = mc.run_id WHERE {sql_where} "
        "ORDER BY mc.ts DESC LIMIT %s OFFSET %s", params + [limit, offset]).fetchall()
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/api/traffic")
def list_all_traffic(run: str = "", limit: int = 100, offset: int = 0, conn=Depends(get_conn),
                     user: str = Depends(current_user)):
    """자체 레코딩 프록시(proxy.http/proxy.https)가 남긴 요청/응답을 전체 진단 범위로 모은 목록
    (runs/{run_ref}/traffic 과 같은 필터, 범위만 넓힘)."""
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    where, params = ["e.tool IN ('proxy.http','proxy.https')", "r.deleted_at IS NULL"], []
    if run.strip():
        where.append("r.run_code ILIKE %s")
        params.append(f"%{run.strip()}%")
    sql_where = " AND ".join(where)
    total = conn.execute(f"SELECT count(*) AS n FROM evidence e JOIN runs r ON r.id = e.run_id WHERE {sql_where}",
                         params).fetchone()["n"]
    rows = conn.execute(
        "SELECT e.id, e.run_id, e.tool, e.args, e.collected_at, e.success, e.size, e.media_type, r.run_code "
        f"FROM evidence e JOIN runs r ON r.id = e.run_id WHERE {sql_where} "
        "ORDER BY e.collected_at DESC LIMIT %s OFFSET %s", params + [limit, offset]).fetchall()
    return {"items": rows, "total": total, "limit": limit, "offset": offset}
