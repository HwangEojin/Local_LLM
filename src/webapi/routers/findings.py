from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from audit.models import AuditError
from diagnostic_workbench import db as wb_db, worker

from ..deps import current_user, get_conn

router = APIRouter(prefix="/api/runs/{run_ref}/findings", tags=["findings"])


def _load_run(conn, run_ref: str) -> dict:
    try:
        return wb_db.resolve_run(conn, run_ref)
    except AuditError as e:
        raise HTTPException(404, str(e)) from None


def _load_finding(conn, run_id, finding_ref: str) -> dict:
    rows = conn.execute("SELECT * FROM findings WHERE run_id=%s AND id::text LIKE %s",
                        (run_id, finding_ref.replace("%", "") + "%")).fetchall()
    if len(rows) != 1:
        raise HTTPException(404, f"finding '{finding_ref}' 을(를) 하나로 특정할 수 없음 ({len(rows)}건)")
    return rows[0]


@router.get("")
def list_findings(run_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    return conn.execute("SELECT * FROM findings WHERE run_id=%s ORDER BY created_at DESC", (run["id"],)).fetchall()


class ReviewBody(BaseModel):
    status: str
    reason: str
    evidence_ids: list[str] = []


@router.post("/{finding_ref}/review")
def review_finding(run_ref: str, finding_ref: str, body: ReviewBody,
                   conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    f = _load_finding(conn, run["id"], finding_ref)
    if body.status not in worker.FINDING_STATUSES:
        raise HTTPException(422, f"status 는 {worker.FINDING_STATUSES} 중 하나여야 함")
    old_status = f["status"]
    try:
        worker.set_finding_status(conn, f["id"], body.status, body.reason,
                                  actor=f"reviewer:{user}", evidence_ids=body.evidence_ids)
    except AuditError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from None
    wb_db.audit(conn, run["id"], "finding_status_changed",
               {"finding_id": str(f["id"]), "old": old_status, "new": body.status, "reviewer": user})
    conn.commit()
    return {"id": str(f["id"]), "old_status": old_status, "new_status": body.status}


class RetestBody(BaseModel):
    reason: str


@router.post("/{finding_ref}/retest")
def retest_finding(run_ref: str, finding_ref: str, body: RetestBody,
                   conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    f = _load_finding(conn, run["id"], finding_ref)
    try:
        item_ids = worker.create_retest(conn, run["id"], f["id"], operator=user, reason=body.reason)
    except AuditError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from None
    wb_db.audit(conn, run["id"], "finding_retest_requested",
               {"finding_id": str(f["id"]), "reviewer": user, "backlog_items": [str(i) for i in item_ids]})
    wb_db.notify_change(conn, run["id"], "fact")
    conn.commit()
    return {"id": str(f["id"]), "backlog_items": [str(i) for i in item_ids]}
