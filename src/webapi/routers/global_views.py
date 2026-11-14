"""진단을 가로지르는 조회 전용 화면: 발견 사항/트래픽/자산/실행 비교(UPGRADE_PLAN.md B2, G5-b, G6 —
commands.py 와 같은 원칙).

새 진단 로직 없음 — 기존 테이블(findings/evidence/assets)을 runs 와 조인해 전체 범위로 보여줄 뿐이다. 상세·검토·
재검증 같은 쓰기 동작은 여전히 run 범위 엔드포인트(/api/runs/{run_ref}/findings/{id} 등)를 그대로 쓴다
(각 행이 들고 있는 run_id 로 호출 — 새 쓰기 경로 없음)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from diagnostic_workbench import compare as wb_compare

from ..deps import current_user, get_conn, load_run

router = APIRouter(tags=["findings", "traffic"])


@router.get("/api/findings")
def list_all_findings(q: str = "", status: str | None = None, include_duplicates: bool = False, limit: int = 100,
                      offset: int = 0, conn=Depends(get_conn), user: str = Depends(current_user)):
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    where, params = ["r.deleted_at IS NULL"], []
    if not include_duplicates:
        where.append("f.duplicate_of IS NULL")
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
        f"SELECT f.*, r.run_code, "
        f"  (SELECT count(*) FROM findings d WHERE d.run_id=f.run_id AND d.locator_hash=f.locator_hash "
        f"   AND d.locator_hash IS NOT NULL AND d.duplicate_of IS NULL AND d.id<>f.id) AS suspected_duplicates, "
        f"  (SELECT count(*) FROM findings m WHERE m.duplicate_of=f.id) AS merged_count "
        f"FROM findings f JOIN runs r ON r.id = f.run_id WHERE {sql_where} "
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


@router.get("/api/assets")
def list_all_assets(company: str = "", kind: str = "", q: str = "", limit: int = 100, offset: int = 0,
                    conn=Depends(get_conn), user: str = Depends(current_user)):
    """전역 자산 화면(G5-b, UPGRADE_PLAN.md) 목록 — 자산별 관찰 실행 수·중복 제외 finding 수·최근 관찰
    시각. 새 진단 로직 없음(기존 assets/run_assets/evidence/findings 를 조인해 집계만 함)."""
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    where, params = ["1=1"], []
    if company.strip():
        where.append("a.company = %s")
        params.append(company.strip())
    if kind.strip():
        where.append("a.kind = %s")
        params.append(kind.strip())
    if q.strip():
        where.append("a.value ILIKE %s")
        params.append(f"%{q.strip()}%")
    sql_where = " AND ".join(where)
    total = conn.execute(f"SELECT count(*) AS n FROM assets a WHERE {sql_where}", params).fetchone()["n"]
    rows = conn.execute(
        "SELECT a.*, "
        "  (SELECT count(DISTINCT ra.run_id) FROM run_assets ra JOIN runs r ON r.id = ra.run_id "
        "   WHERE ra.asset_id=a.id AND r.deleted_at IS NULL) AS observed_runs, "
        "  (SELECT count(*) FROM findings f JOIN runs r ON r.id = f.run_id "
        "   WHERE f.asset_id=a.id AND f.duplicate_of IS NULL AND r.deleted_at IS NULL) AS finding_count, "
        "  (SELECT max(e.collected_at) FROM evidence e JOIN runs r ON r.id = e.run_id "
        "   WHERE e.asset_id=a.id AND r.deleted_at IS NULL) AS last_observed_at "
        f"FROM assets a WHERE {sql_where} ORDER BY a.value LIMIT %s OFFSET %s",
        params + [limit, offset]).fetchall()
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


def _load_asset(conn, asset_ref: str) -> dict:
    row = conn.execute("SELECT * FROM assets WHERE id::text = %s", (asset_ref,)).fetchone()
    if row is None:
        raise HTTPException(404, f"자산 '{asset_ref}' 을(를) 찾을 수 없음")
    return row


@router.get("/api/assets/{asset_ref}")
def get_asset_detail(asset_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    """자산 상세: 양방향 관계, 관찰한 실행 이력, 실행별 finding(중복 제외, fingerprint 로 분류 가능)."""
    asset = _load_asset(conn, asset_ref)
    edges = conn.execute(
        "SELECT e.id, 'out' AS direction, e.rel, d.id AS other_id, d.kind AS other_kind, "
        "  d.value AS other_value, e.source "
        "FROM asset_edges e JOIN assets d ON d.id = e.dst_id WHERE e.src_id = %(id)s "
        "UNION ALL "
        "SELECT e.id, 'in' AS direction, e.rel, s.id AS other_id, s.kind AS other_kind, "
        "  s.value AS other_value, e.source "
        "FROM asset_edges e JOIN assets s ON s.id = e.src_id WHERE e.dst_id = %(id)s "
        "ORDER BY other_value", {"id": asset["id"]}).fetchall()
    runs = conn.execute(
        "SELECT r.id, r.run_code, r.status, r.created_at, ra.target_id FROM run_assets ra "
        "JOIN runs r ON r.id = ra.run_id WHERE ra.asset_id = %s AND r.deleted_at IS NULL "
        "ORDER BY r.created_at DESC", (asset["id"],)).fetchall()
    findings = conn.execute(
        "SELECT f.*, r.run_code FROM findings f JOIN runs r ON r.id = f.run_id "
        "WHERE f.asset_id = %s AND f.duplicate_of IS NULL AND r.deleted_at IS NULL "
        "ORDER BY f.created_at DESC", (asset["id"],)).fetchall()
    return {"asset": asset, "edges": edges, "runs": runs, "findings": findings}


@router.get("/api/compare")
def compare_runs(base: str, head: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    """실행 간 비교(G6, UPGRADE_PLAN.md) — 신규/지속/재발/미재현(조치 추정)/미확인/비교 불가.
    새 진단 로직 없음(compare.compare_runs 가 기존 findings/evidence/finding_review_history 만 읽음)."""
    base_run, head_run = load_run(conn, base), load_run(conn, head)
    base_companies, head_companies = (wb_compare.run_companies(conn, base_run["id"]),
                                      wb_compare.run_companies(conn, head_run["id"]))
    if base_companies and head_companies and not (base_companies & head_companies):
        raise HTTPException(422, "두 실행의 자산 company 가 달라 비교할 수 없음")
    result = wb_compare.compare_runs(conn, base_run["id"], head_run["id"])
    return {
        "base_run": {"id": base_run["id"], "run_code": base_run["run_code"]},
        "head_run": {"id": head_run["id"], "run_code": head_run["run_code"]},
        **result,
    }
