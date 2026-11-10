"""Triggers(F3b-1, UPGRADE_PLAN.md): LLM 을 부르지 않는 결정론적 규칙. enrich.py 와 같은 패턴 — planner 가
매 계획 주기에 스스로 조건을 보고 작업을 큐에 올린다. 그 작업은 기존 backlog_items/policy.decide() 경로를
그대로 거치므로(exploration 종류로 삽입) 새 실행 경로나 별도 스케줄러가 없다. planner.plan() 바깥에서 이
함수를 부르는 코드가 없는 한 CLAUDE.md §5/§8 의 "예산·Kill Switch 가 그 루프 안을 전제"하는 조건이
설계만으로 자동 만족된다(run_loop/_wait_for_wake 가 먼저 stop_file/deadline/CANCELLED 를 확인한 뒤에만
planner.plan() 을 부름, policy.decide() 자체도 stop_file 을 독립적으로 재확인함).
"""
from __future__ import annotations

from psycopg.types.json import Jsonb


def _fired_interval(conn, t: dict) -> str | None:
    seconds = (t["condition_args"] or {}).get("seconds", 300)
    row = conn.execute("SELECT now() - %s AS elapsed", (t["last_checked_at"],)).fetchone()
    if row["elapsed"].total_seconds() < seconds:
        return None
    return str(int(row["elapsed"].total_seconds()))  # 같은 틱에 두 번 발동하지 않게 last_checked_at 을 바로 갱신함


def _fired_on_finding(conn, t: dict) -> str | None:
    row = conn.execute("SELECT id FROM findings WHERE run_id=%s AND created_at > %s ORDER BY created_at LIMIT 1",
                       (t["run_id"], t["last_checked_at"])).fetchone()
    return str(row["id"]) if row else None


def _fired_on_task_create(conn, t: dict) -> str | None:
    kind = (t["condition_args"] or {}).get("kind")
    row = conn.execute(
        "SELECT id FROM backlog_items WHERE run_id=%s AND created_at > %s AND (%s::text IS NULL OR kind=%s) "
        "ORDER BY created_at LIMIT 1", (t["run_id"], t["last_checked_at"], kind, kind)).fetchone()
    return str(row["id"]) if row else None


def _fired_on_task_timeout(conn, t: dict) -> str | None:
    """도구가 '시간 초과'로 실패한 호출(tools.py 의 ToolError 문구 패턴)만 본다 — 작업이 lease 시간 안에
    끝나지 않아 재임대된 경우(backlog.reclaim_expired)는 보통 다음 Worker 가 다시 집기만 하므로 제외."""
    row = conn.execute(
        "SELECT id FROM tool_invocations WHERE run_id=%s AND status='FAILED' AND started_at > %s "
        "AND error ILIKE '%%시간 초과%%' ORDER BY started_at LIMIT 1", (t["run_id"], t["last_checked_at"])).fetchone()
    return str(row["id"]) if row else None


def _fired_on_tool_call(conn, t: dict) -> str | None:
    tool = (t["condition_args"] or {}).get("tool")
    if not tool:
        return None
    row = conn.execute(
        "SELECT id FROM tool_invocations WHERE run_id=%s AND tool=%s AND started_at > %s AND status='SUCCEEDED' "
        "ORDER BY started_at LIMIT 1", (t["run_id"], tool, t["last_checked_at"])).fetchone()
    return str(row["id"]) if row else None


def _fired_on_goal_met(conn, t: dict) -> str | None:
    row = conn.execute(
        "SELECT count(*) AS n FROM run_checklist_items WHERE run_id=%s AND verdict IN ('NOT_RUN','INCONCLUSIVE')",
        (t["run_id"],)).fetchone()
    if row["n"] > 0:
        return None
    return "goal_met"  # 체크리스트 전부가 끝난 상태는 한 번만 봐도 되므로 고정 키(같은 실행에서 재발동 안 함)


_CHECK = {"interval": _fired_interval, "on_finding": _fired_on_finding, "on_goal_met": _fired_on_goal_met,
         "on_task_timeout": _fired_on_task_timeout, "on_tool_call": _fired_on_tool_call,
         "on_task_create": _fired_on_task_create}


def evaluate(ctx) -> int:
    """활성 트리거를 조건별로 확인해 작업을 큐에 올린다. 반환값은 새로 큐에 올린 건수."""
    conn, run_id = ctx.conn, ctx.run["id"]
    rows = conn.execute("SELECT * FROM run_triggers WHERE run_id=%s AND enabled", (run_id,)).fetchall()
    if not rows:
        return 0
    goal = conn.execute("SELECT id FROM exploration_nodes WHERE run_id=%s AND kind='goal' ORDER BY created_at LIMIT 1",
                        (run_id,)).fetchone()
    accepted = 0
    for t in rows:
        check = _CHECK[t["condition_kind"]]
        fired = check(conn, t)
        conn.execute("UPDATE run_triggers SET last_checked_at=now() WHERE id=%s", (t["id"],))
        if fired is None:
            continue
        key = f"trigger:{t['id']}:{fired}"
        if conn.execute("SELECT 1 FROM backlog_items WHERE run_id=%s AND idempotency_key=%s",
                        (run_id, key)).fetchone():
            continue
        node = conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'intent','trigger',%s) "
            "RETURNING id", (run_id, Jsonb({"trigger_id": t["id"], "trigger_name": t["name"],
                                           "tool": t["tool"], "args": t["args"]}))).fetchone()["id"]
        if goal:
            conn.execute("INSERT INTO exploration_edges VALUES (%s,%s,'spawns')", (goal["id"], node))
        if t["target_asset_id"]:
            conn.execute("INSERT INTO exploration_anchors (node_id, asset_id) VALUES (%s,%s)",
                        (node, t["target_asset_id"]))
        conn.execute(
            "INSERT INTO backlog_items (run_id, kind, exploration_node_id, asset_id, priority, idempotency_key, "
            "reason, payload, max_attempts) VALUES (%s,'exploration',%s,%s,70,%s,%s,%s,1) "
            "ON CONFLICT (run_id, idempotency_key) DO NOTHING",
            (run_id, node, t["target_asset_id"], key, f"트리거 '{t['name']}' 발동",
             Jsonb({"tool": t["tool"], "args": t["args"]})))
        accepted += 1
    return accepted
