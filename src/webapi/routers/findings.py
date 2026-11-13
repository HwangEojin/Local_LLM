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
_EXPORT_COLS = ("title", "status", "verification", "asset_id", "rationale", "remediation",
                "reviewed_by", "reviewed_at", "review_reason", "created_at")


def _load_finding(conn, run_id, finding_ref: str) -> dict:
    try:
        return wb_db.resolve_by_prefix(conn, "findings", run_id, finding_ref, "finding")
    except AuditError as e:
        raise HTTPException(404, str(e)) from None


@router.get("")
def list_findings(run_ref: str, include_duplicates: bool = False, conn=Depends(get_conn),
                  user: str = Depends(current_user)):
    """G1: 기본적으로 병합된(duplicate_of 가 있는) finding 은 숨긴다. 자기 자신을 뺀 같은 run·같은
    locator_hash 건수를 suspected_duplicates 로 붙인다(D2 "중복 의심" — 자동 병합된 건 fingerprint 가
    같아 이미 한 행이라 여기 안 잡힘)."""
    run = _load_run(conn, run_ref)
    where = "run_id=%s" if include_duplicates else "run_id=%s AND duplicate_of IS NULL"
    return conn.execute(
        f"SELECT f.*, "
        f"  (SELECT count(*) FROM findings d WHERE d.run_id=f.run_id AND d.locator_hash=f.locator_hash "
        f"   AND d.locator_hash IS NOT NULL AND d.duplicate_of IS NULL AND d.id<>f.id) AS suspected_duplicates, "
        f"  (SELECT count(*) FROM findings m WHERE m.duplicate_of=f.id) AS merged_count "
        f"FROM findings f WHERE {where} ORDER BY created_at DESC", (run["id"],)).fetchall()


@router.get("/{finding_ref}/similar")
def similar_findings(run_ref: str, finding_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    """D2 중복 의심 후보: 같은 run·같은 locator_hash·자기 자신 제외·아직 병합 안 된 finding."""
    run = _load_run(conn, run_ref)
    f = _load_finding(conn, run["id"], finding_ref)
    if not f["locator_hash"]:
        return []
    return conn.execute(
        "SELECT id, title, vuln_class, status, "
        "(SELECT count(*) FROM finding_evidence fe WHERE fe.finding_id=findings.id) AS evidence_count "
        "FROM findings WHERE run_id=%s AND locator_hash=%s AND duplicate_of IS NULL AND id<>%s "
        "ORDER BY created_at", (run["id"], f["locator_hash"], f["id"])).fetchall()


@router.get("/export")
def export_findings(run_ref: str, format: str = "csv", status: str | None = None, conn=Depends(get_conn),
                    user: str = Depends(current_user)):
    """발견 사항을 CSV/JSON 으로 내려받는다(레퍼런스 프로젝트 의 findings export 참고). 새 진단 로직 없음 — findings 테이블을
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
    votes = conn.execute(  # G2: 제안/검증 모델별 판정
        "SELECT model, stage, verdict, rationale, evidence_ids, created_at "
        "FROM finding_llm_votes WHERE finding_id=%s ORDER BY created_at", (f["id"],)
    ).fetchall()
    return {"finding": f, "evidence": evidence, "history": history, "llm_votes": votes}


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
        notify.notify_finding_confirmed(conn, cfg, run["id"], f["id"], title=f["title"],
                                        actor=f"reviewer:{user}")
    conn.commit()
    return {"id": str(f["id"]), "old_status": old_status, "new_status": body.status}


class MergeBody(BaseModel):
    into: str
    reason: str


class UnmergeBody(BaseModel):
    reason: str


@router.post("/{finding_ref}/merge")
def merge_finding(run_ref: str, finding_ref: str, body: MergeBody,
                  conn=Depends(get_conn), user: str = Depends(current_user)):
    """G1 D2 수동 병합: 사람이 '중복 의심' 후보 중 진짜 중복인 걸 하나로 합친다. 원본 행·증거 링크는
    보존하고(§11 기존 데이터 보존) duplicate_of 만 세운다 — 대상(into)에도 원본 증거를 링크해 목록·보고서가
    대상 쪽만 봐도 전부 보이게 한다."""
    run = _load_run(conn, run_ref)
    f = _load_finding(conn, run["id"], finding_ref)
    into = _load_finding(conn, run["id"], body.into)
    if into["id"] == f["id"]:
        raise HTTPException(422, "자기 자신으로는 병합할 수 없습니다")
    if into["duplicate_of"] is not None:
        raise HTTPException(422, "병합 대상이 이미 다른 finding 의 중복입니다(체인 금지) — 그 원본으로 병합하세요")
    evidence_ids = [r["evidence_id"] for r in
                    conn.execute("SELECT evidence_id FROM finding_evidence WHERE finding_id=%s", (f["id"],))]
    for eid in evidence_ids:
        conn.execute("INSERT INTO finding_evidence VALUES (%s,%s) ON CONFLICT DO NOTHING", (into["id"], eid))
    conn.execute("UPDATE findings SET duplicate_of=%s WHERE id=%s", (into["id"], f["id"]))
    for fid, status, other, direction in ((f["id"], f["status"], into["id"], "다음으로 병합됨"),
                                          (into["id"], into["status"], f["id"], "다음을 병합함")):
        conn.execute(
            "INSERT INTO finding_review_history (finding_id, old_status, new_status, reason, actor, evidence_ids) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (fid, status, status, f"{direction}: {other}. {body.reason}", f"reviewer:{user}", evidence_ids))
    wb_db.audit(conn, run["id"], "finding_merged",
               {"finding_id": str(f["id"]), "into": str(into["id"]), "reviewer": user, "reason": body.reason})
    conn.commit()
    return {"id": str(f["id"]), "duplicate_of": str(into["id"])}


@router.post("/{finding_ref}/unmerge")
def unmerge_finding(run_ref: str, finding_ref: str, body: UnmergeBody,
                    conn=Depends(get_conn), user: str = Depends(current_user)):
    """병합을 되돌린다. 증거 링크는 추가만 하는 원칙이라 되돌리지 않는다 — 병합 중 대상에 걸린 링크는 그대로
    남는다(지워지는 게 더 위험함, §11)."""
    run = _load_run(conn, run_ref)
    f = _load_finding(conn, run["id"], finding_ref)
    if f["duplicate_of"] is None:
        raise HTTPException(422, "이 finding 은 병합된 상태가 아닙니다")
    old_into = f["duplicate_of"]
    conn.execute("UPDATE findings SET duplicate_of=NULL WHERE id=%s", (f["id"],))
    conn.execute(
        "INSERT INTO finding_review_history (finding_id, old_status, new_status, reason, actor, evidence_ids) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (f["id"], f["status"], f["status"], f"병합 해제(이전 대상: {old_into}). {body.reason}",
         f"reviewer:{user}", []))
    wb_db.audit(conn, run["id"], "finding_unmerged",
               {"finding_id": str(f["id"]), "was_duplicate_of": str(old_into), "reviewer": user, "reason": body.reason})
    conn.commit()
    return {"id": str(f["id"])}


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
