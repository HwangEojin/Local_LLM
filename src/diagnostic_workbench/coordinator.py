"""실행 조정기: 계획 -> 점유 -> 실행 -> (변경 감지) 재계획 반복. 큐가 비거나 한도·중지·취소에서 멈춘다."""
from __future__ import annotations

import os
import socket
import time

from audit.models import AuditError

from . import backlog, db, explore, planner, worker
from .runs import build_context


_DEBOUNCE_S = 0.8   # 거의 동시에 들어온 이벤트 여러 건을 한 번의 재계획으로 묶는다
_HEARTBEAT_S = 600  # 이벤트를 놓쳤어도 이 간격마다 한 번은 다시 계획을 시도한다(폴백)
_POLL_S = 0.5       # DB 폴링 간격(Postgres LISTEN/NOTIFY 는 쓰지 않음 — change_events 테이블을 그냥 본다)


def _stop_reason(conn, run_id, cfg, deadline) -> str | None:
    if conn.execute("SELECT status FROM runs WHERE id=%s", (run_id,)).fetchone()["status"] == "CANCELLED":
        return "CANCELLED"
    if cfg.stop_file.exists():
        return f"전체 중지 스위치 ({cfg.stop_file})"
    if time.monotonic() > deadline:
        return "시간 예산(timeout_s) 초과"
    return None


def _wait_for_wake(ctx, conn, run_id, cfg, name, deadline):
    """할 일이 당장 없을 때 기다린다. 이벤트+디바운스(800ms)+하트비트(600s) 로 깨어나 다시 계획해 본다
    (Postgres 를 짧은 간격으로 폴링한다 — 거의 동시에 온 이벤트를 한 번에 묶고, 이벤트를 놓쳐도 하트비트가
    안전망이 된다). 새 작업을 찾으면 (작업, None) 을,
    끝내야 하면 (None, 사유) 를 돌려준다. 하트비트에도 새 작업이 없으면 (None, "NO_WORK") — "유효한
    후속 작업 없음"(CLAUDE.md §5.4)이며 PAUSED 가 아니라 기존과 같이 COMPLETED 로 이어진다.
    """
    heartbeat_at = time.monotonic() + _HEARTBEAT_S
    debounce_until = None
    while True:
        stop = _stop_reason(conn, run_id, cfg, deadline)
        if stop:
            return None, stop
        now = time.monotonic()
        has_event = conn.execute("SELECT 1 FROM change_events WHERE run_id=%s AND processed_at IS NULL LIMIT 1",
                                 (run_id,)).fetchone() is not None
        if has_event and debounce_until is None:
            debounce_until = now + _DEBOUNCE_S
        heartbeat_due = now > heartbeat_at
        if heartbeat_due or (has_event and debounce_until is not None and now >= debounce_until):
            planner.plan(ctx)
            item = backlog.claim(conn, run_id, name, cfg.lease_s)
            if item is not None:
                return item, None
            if heartbeat_due:
                return None, "NO_WORK"
            heartbeat_at, debounce_until = time.monotonic() + _HEARTBEAT_S, None  # 재계획했지만 아직 없음 — 계속 대기
        time.sleep(_POLL_S)


def _set_status(conn, run_id, status, reason=None, ended=False):
    conn.execute("UPDATE runs SET status=%s, stop_reason=%s, updated_at=now(), "
                 "started_at=COALESCE(started_at, now()), ended_at=CASE WHEN %s THEN now() ELSE NULL END WHERE id=%s",
                 (status, reason, ended, run_id))
    db.audit(conn, run_id, f"run_{status.lower()}", {"reason": reason})
    conn.commit()


def run_loop(cfg, conn, run: dict, log=print) -> dict:
    """실행을 시작하거나 이어서 진행한다. 최종 runs 행을 반환한다.

    COMPLETED 는 '마지막으로 본 시점에 더 할 일이 없었다'는 뜻일 뿐 확정된 종료가 아니다 — 그 뒤 운영자가
    증적을 더 모으거나 탐색이 새 사실을 내면 다시 할 일이 생길 수 있으므로 재개를 허용한다. CANCELLED 만
    사람이 명시적으로 끝낸 상태라 재개하지 않는다.
    """
    if run["status"] == "CANCELLED":
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
                item, wait_stop = _wait_for_wake(ctx, conn, run_id, cfg, name, deadline)
                if wait_stop == "CANCELLED":
                    conn.commit()
                    return db.resolve_run(conn, str(run_id))
                if wait_stop == "NO_WORK":
                    break  # stop 은 그대로 None -> 기존과 같이 아래에서 COMPLETED 로 판정
                if wait_stop:
                    stop = wait_stop
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
