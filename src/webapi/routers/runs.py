from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from audit.models import AuditError
from diagnostic_workbench import backlog, db as wb_db, reporting

from ..deps import current_user, get_conn

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("")
def list_runs(conn=Depends(get_conn), user: str = Depends(current_user)):
    rows = conn.execute(
        "SELECT r.id, r.status, r.operator, r.autonomy, r.use_llm, r.created_at, r.stop_reason, "
        "r.checklist_version_id IS NOT NULL AS has_checklist, "
        "(SELECT count(*) FROM findings f WHERE f.run_id = r.id) AS findings_count "
        "FROM runs r ORDER BY r.created_at DESC LIMIT 50").fetchall()
    return rows


def _load_run(conn, run_ref: str) -> dict:
    try:
        return wb_db.resolve_run(conn, run_ref)
    except AuditError as e:
        raise HTTPException(404, str(e)) from None


@router.get("/{run_ref}")
def get_run(run_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    cov = reporting.coverage(conn, run["id"])
    counts = backlog.counts(conn, run["id"])
    n = conn.execute(
        "SELECT (SELECT count(*) FROM tool_invocations ti JOIN backlog_items b ON b.id=ti.backlog_item_id "
        " WHERE ti.run_id=%(r)s AND ti.status<>'SKIPPED' AND b.kind='checklist') AS tools, "
        "(SELECT count(*) FROM tool_invocations ti JOIN backlog_items b ON b.id=ti.backlog_item_id "
        " WHERE ti.run_id=%(r)s AND ti.status<>'SKIPPED' AND b.kind='exploration') AS explore_tools, "
        "(SELECT count(*) FROM evidence WHERE run_id=%(r)s AND success) AS evidence, "
        "(SELECT count(*) FROM findings WHERE run_id=%(r)s) AS findings, "
        "(SELECT count(*) FROM model_calls WHERE run_id=%(r)s) AS llm_calls",
        {"r": run["id"]}).fetchone()
    return {"run": run, "coverage": {"verdicts": cov["verdicts"], "backlog": counts}, "usage": n}


@router.get("/{run_ref}/backlog")
def get_backlog(run_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    rows = conn.execute(
        "SELECT id, kind, status, reason, policy_class, blocked_reason, attempts, created_at, updated_at "
        "FROM backlog_items WHERE run_id=%s ORDER BY created_at DESC LIMIT 200", (run["id"],)).fetchall()
    return rows
