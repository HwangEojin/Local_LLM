from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from audit.models import AuditError
from diagnostic_workbench import db as wb_db, notify, worker

from .. import deps
from ..deps import current_user, get_cfg, get_conn

router = APIRouter(prefix="/api/runs/{run_ref}/findings", tags=["findings"])

_load_run = deps.load_run
_EXPORT_COLS = ("title", "severity", "status", "verification", "asset_id", "rationale", "remediation",
                "reviewed_by", "reviewed_at", "review_reason", "created_at")


def _load_finding(conn, run_id, finding_ref: str) -> dict:
    try:
        return wb_db.resolve_by_prefix(conn, "findings", run_id, finding_ref, "finding")
    except AuditError as e:
        raise HTTPException(404, str(e)) from None


@router.get("")
def list_findings(run_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    return conn.execute("SELECT * FROM findings WHERE run_id=%s ORDER BY created_at DESC", (run["id"],)).fetchall()


@router.get("/export")
def export_findings(run_ref: str, format: str = "csv", status: str | None = None, conn=Depends(get_conn),
                    user: str = Depends(current_user)):
    """발견 사항을 CSV/JSON 으로 내려받는다(artex 의 findings export 참고). 새 진단 로직 없음 — findings 테이블을
    그대로 읽어 모양만 바꾼다. status 를 안 주면 REJECTED 도 포함한다(여기서는 조회·보관 목적이라 §3.3 의
    '기각된 후보를 발견처럼 싣지 않는다'는 Word 보고서 본문에만 적용 — CSV 는 기각 사유까지 감사용으로 남긴다)."""
    if format not in ("csv", "json"):
        raise HTTPException(422, "format 은 csv 또는 json 이어야 합니다")
    run = _load_run(conn, run_ref)
    where, params = ["run_id=%s"], [run["id"]]
    if status:
        where.append("status=%s")
        params.append(status)
    rows = conn.execute(f"SELECT * FROM findings WHERE {' AND '.join(where)} ORDER BY created_at",
                        params).fetchall()
    stamp = run["run_code"]
    if format == "json":
        body = json.dumps([{k: (str(r[k]) if r[k] is not None else None) for k in _EXPORT_COLS} for r in rows],
                          ensure_ascii=False, indent=2)
        return Response(body, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="findings_{stamp}.json"'})
    buf = io.StringIO()
    buf.write("﻿")  # BOM: 엑셀이 한글 CSV 를 UTF-8 로 바로 열게
    w = csv.writer(buf)
    w.writerow(_EXPORT_COLS)
    for r in rows:
        w.writerow([r[c] for c in _EXPORT_COLS])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="findings_{stamp}.csv"'})


@router.get("/{finding_ref}")
def get_finding(run_ref: str, finding_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    """상세 패널용: finding + 연결된 증적(메타만) + 검토 이력. 읽기 전용."""
    run = _load_run(conn, run_ref)
    f = _load_finding(conn, run["id"], finding_ref)
    evidence = conn.execute(
        "SELECT e.id, e.tool, e.success, e.size, e.media_type, e.collected_at FROM finding_evidence fe "
        "JOIN evidence e ON e.id = fe.evidence_id WHERE fe.finding_id=%s ORDER BY e.collected_at", (f["id"],)
    ).fetchall()
    history = conn.execute(
        "SELECT old_status, new_status, reason, actor, evidence_ids, changed_at "
        "FROM finding_review_history WHERE finding_id=%s ORDER BY changed_at DESC", (f["id"],)
    ).fetchall()
    return {"finding": f, "evidence": evidence, "history": history}


class ReviewBody(BaseModel):
    status: str
    reason: str
    evidence_ids: list[str] = []


@router.post("/{finding_ref}/review")
def review_finding(run_ref: str, finding_ref: str, body: ReviewBody,
                   conn=Depends(get_conn), cfg=Depends(get_cfg), user: str = Depends(current_user)):
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
    if body.status == "CONFIRMED":
        notify.notify_finding_confirmed(conn, cfg, run["id"], f["id"], title=f["title"], severity=f["severity"],
                                        actor=f"reviewer:{user}")
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
