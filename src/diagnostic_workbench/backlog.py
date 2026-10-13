"""공유 TodoList(작업 큐): 허용된 상태 전이, 원자적 점유(SKIP LOCKED), 임대 만료 복구."""
from __future__ import annotations

from audit.models import AuditError

# 새 상태 -> 그 상태로 올 수 있는 이전 상태. 여기에 없는 전이는 거부한다.
ALLOWED_FROM = {
    "READY": {"PENDING", "BLOCKED", "RUNNING", "FAILED"},  # RUNNING: 임대 만료/중단, FAILED: 명시적 재시도
    "BLOCKED": {"PENDING", "READY", "RUNNING"},
    "RUNNING": {"READY"},
    "SUCCEEDED": {"RUNNING"},
    "FAILED": {"RUNNING"},
    "NEEDS_REVIEW": {"RUNNING"},
    "CANCELLED": {"PENDING", "READY", "BLOCKED", "RUNNING"},
}
OPEN = ("PENDING", "READY", "RUNNING", "BLOCKED")
DONE = ("SUCCEEDED", "NEEDS_REVIEW")  # 선행 작업으로서 '충족'으로 보는 상태


def transition(conn, item_id, new: str, **fields) -> dict:
    """상태를 바꾸고 바뀐 행을 반환한다. 허용되지 않은 전이면 AuditError."""
    sets = "".join(f", {k} = %s" for k in fields)
    row = conn.execute(
        f"UPDATE backlog_items SET status = %s, updated_at = now(){sets} "
        "WHERE id = %s AND status = ANY(%s) RETURNING *",
        (new, *fields.values(), item_id, list(ALLOWED_FROM[new]))).fetchone()
    if row is None:
        cur = conn.execute("SELECT status FROM backlog_items WHERE id = %s", (item_id,)).fetchone()
        raise AuditError(f"작업 {item_id}: {cur['status'] if cur else '없음'} -> {new} 전이는 허용되지 않음")
    return row


def reclaim_expired(conn, run_id) -> int:
    """Worker 가 죽어 임대가 만료된 RUNNING 작업을 되돌린다(시도 횟수를 다 썼으면 FAILED)."""
    return conn.execute(
        "UPDATE backlog_items SET status = CASE WHEN attempts >= max_attempts THEN 'FAILED' ELSE 'READY' END, "
        "last_error = '임대 만료(Worker 응답 없음)', lease_owner = NULL, lease_until = NULL, updated_at = now() "
        "WHERE run_id = %s AND status = 'RUNNING' AND lease_until < now()", (run_id,)).rowcount


def claim(conn, run_id, worker: str, lease_s: int) -> dict | None:
    """READY 작업 하나를 원자적으로 점유한다. 다른 Worker 가 잡은 행은 건너뛴다."""
    row = conn.execute(
        "UPDATE backlog_items SET status = 'RUNNING', attempts = attempts + 1, lease_owner = %s, "
        "lease_until = now() + make_interval(secs => %s), updated_at = now() "
        "WHERE id = (SELECT id FROM backlog_items WHERE run_id = %s AND status = 'READY' "
        "            ORDER BY priority, created_at, id FOR UPDATE SKIP LOCKED LIMIT 1) "
        "RETURNING *", (worker, lease_s, run_id)).fetchone()
    conn.commit()
    return row


def counts(conn, run_id) -> dict:
    return {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, count(*) AS n FROM backlog_items WHERE run_id = %s GROUP BY status", (run_id,))}
