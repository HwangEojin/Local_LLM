"""실행 조정기: 계획 -> 점유 -> 실행 -> (변경 감지) 재계획 반복. 큐가 비거나 한도·중지·취소에서 멈춘다."""
from __future__ import annotations

import os
import socket
import time

from audit.models import AuditError

from . import backlog, db, explore, planner, worker
from .runs import build_context


def _set_status(conn, run_id, status, reason=None, ended=False):
    conn.execute("UPDATE runs SET status=%s, stop_reason=%s, updated_at=now(), "
                 "started_at=COALESCE(started_at, now()), ended_at=CASE WHEN %s THEN now() ELSE NULL END WHERE id=%s",
                 (status, reason, ended, run_id))
    db.audit(conn, run_id, f"run_{status.lower()}", {"reason": reason})
    conn.commit()


def run_loop(cfg, conn, run: dict, log=print) -> dict:
    """실행을 시작하거나 이어서 진행한다. 최종 runs 행을 반환한다."""
    if run["status"] in ("COMPLETED", "CANCELLED"):
        raise AuditError(f"실행 {run['id']} 은(는) 이미 {run['status']} 상태임")
    ctx = build_context(cfg, conn, run)
    if run["use_llm"]:
        ctx.llm = explore.make_llm(ctx)
    run_id, name = run["id"], f"{socket.gethostname()}:{os.getpid()}"
    _set_status(conn, run_id, "RUNNING")
    deadline = time.monotonic() + run["budget"]["timeout_s"]  # ponytail: 'run start' 한 번 기준의 시간 예산
    stop, first = None, True
    try:
        while True:
            if conn.execute("SELECT status FROM runs WHERE id=%s", (run_id,)).fetchone()["status"] == "CANCELLED":
                conn.commit()
                return db.resolve_run(conn, str(run_id))
            if cfg.stop_file.exists():
                stop = f"전체 중지 스위치 ({cfg.stop_file})"
            elif time.monotonic() > deadline:
                stop = "시간 예산(timeout_s) 초과"
            if stop:
                break
            woke = conn.execute("SELECT 1 FROM change_events WHERE run_id=%s AND processed_at IS NULL LIMIT 1",
                                (run_id,)).fetchone()
            if first or woke:
                planner.plan(ctx)
                first = False
            item = backlog.claim(conn, run_id, name, cfg.lease_s)
            if item is None:
                planner.plan(ctx)  # 깨우기 신호를 놓쳤어도 DB 상태에서 복구한다
                item = backlog.claim(conn, run_id, name, cfg.lease_s)
                if item is None:
                    break
            status = worker.execute(ctx, item, name)
            log(f"  [{status:<12}] {item['kind']:<11} {item['reason'][:90]}")
    except KeyboardInterrupt:
        conn.rollback()
        conn.execute("UPDATE backlog_items SET status='READY', attempts=attempts-1, lease_owner=NULL, lease_until=NULL "
                     "WHERE run_id=%s AND status='RUNNING' AND lease_owner=%s", (run_id, name))
        _set_status(conn, run_id, "PAUSED", "운영자가 중단함")
        raise
    c = backlog.counts(conn, run_id)
    waiting = conn.execute("SELECT count(*) AS n FROM backlog_items WHERE run_id=%s AND status='BLOCKED' "
                           "AND policy_class='ALLOW_WITH_APPROVAL'", (run_id,)).fetchone()["n"]
    if stop:
        _set_status(conn, run_id, "PAUSED", stop)
    elif c.get("RUNNING"):
        log("  다른 Worker 가 아직 작업 중이므로 실행 상태를 바꾸지 않음")
    elif waiting:
        _set_status(conn, run_id, "PAUSED", f"승인 대기 {waiting}건")
    else:
        _set_status(conn, run_id, "COMPLETED", ended=True)
    return db.resolve_run(conn, str(run_id))


def cancel(conn, run: dict, operator: str) -> int:
    n = conn.execute("UPDATE backlog_items SET status='CANCELLED', updated_at=now() WHERE run_id=%s "
                     "AND status IN ('PENDING','READY','BLOCKED')", (run["id"],)).rowcount
    conn.execute("UPDATE runs SET status='CANCELLED', stop_reason=%s, updated_at=now(), ended_at=now() WHERE id=%s",
                 (f"{operator} 이(가) 취소함", run["id"]))
    db.audit(conn, run["id"], "run_cancelled", {"operator": operator, "cancelled_items": n})
    conn.commit()
    return n
