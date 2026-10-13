"""변경 기반 Planner. 도구를 실행하지 않는다: 작업을 제안하고, 정책 판정으로 READY/BLOCKED 를 정한다.

같은 입력 상태에서 같은 작업이 다시 생기지 않도록 (run_id, idempotency_key) 고유 제약에 맡긴다.
변경 이벤트는 깨우기 신호일 뿐이며, 매번 DB 의 현재 상태(미실행 판정, 열린 작업)를 다시 읽는다.
"""
from __future__ import annotations

from . import backlog, db, explore, policy, worker

_PRIORITY = "CASE ci.automation_level WHEN 'AUTOMATABLE' THEN 10 WHEN 'PARTIALLY_AUTOMATABLE' THEN 20 ELSE 50 END"


def _propose_checklist_tasks(ctx) -> int:
    run_id = ctx.run["id"]
    room = ctx.run["budget"]["max_tasks"] - ctx.conn.execute(
        "SELECT count(*) AS n FROM backlog_items WHERE run_id=%s", (run_id,)).fetchone()["n"]
    return ctx.conn.execute(
        "INSERT INTO backlog_items (run_id, kind, run_checklist_item_id, asset_id, priority, idempotency_key, "
        "reason, max_attempts) "
        f"SELECT rci.run_id, 'checklist', rci.id, rci.asset_id, {_PRIORITY}, 'checklist:' || rci.id, "
        "'체크리스트 항목 ' || ci.check_id || ' 미실행', %s "
        "FROM run_checklist_items rci JOIN checklist_items ci ON ci.id = rci.checklist_item_id "
        "WHERE rci.run_id = %s AND rci.verdict = 'NOT_RUN' ORDER BY 5, ci.check_id LIMIT %s "
        "ON CONFLICT (run_id, idempotency_key) DO NOTHING",
        (ctx.cfg.max_attempts, run_id, max(0, room))).rowcount


def _admit(ctx) -> dict:
    """PENDING 과 '다시 볼 이유가 있는' BLOCKED(선행 대기, 승인 대기)만 재검사한다. 정책 거부는 그대로 둔다."""
    conn, stats = ctx.conn, {"ready": 0, "blocked": 0}
    rows = conn.execute(
        "SELECT * FROM backlog_items WHERE run_id=%s AND (status='PENDING' OR (status='BLOCKED' AND "
        "(policy_class IS NULL OR policy_class='ALLOW_WITH_APPROVAL'))) ORDER BY priority, created_at",
        (ctx.run["id"],)).fetchall()
    for item in rows:
        unmet = conn.execute(
            "SELECT count(*) AS n FROM backlog_deps d JOIN backlog_items b ON b.id = d.depends_on_id "
            "WHERE d.item_id=%s AND b.status <> ALL(%s)", (item["id"], list(backlog.DONE))).fetchone()["n"]
        if unmet:
            new = ("BLOCKED", None, f"선행 작업 {unmet}건 대기")
        else:
            it = worker.load_item(ctx, item)
            decisions = [(tool, policy.decide(ctx, tool, args, item["asset_id"])) for _, tool, args in worker.calls(it)]
            bad = next(((t, d) for t, d in decisions
                        if d.decision in (policy.DENY, policy.NEEDS_SCOPE_CONFIRMATION)), None) \
                or next(((t, d) for t, d in decisions if d.decision != policy.ALLOW), None)
            new = ("BLOCKED", bad[1].decision, f"{bad[0]}: {bad[1].reason}") if bad else ("READY", policy.ALLOW, None)
            if bad and bad[1].decision != policy.ALLOW_WITH_APPROVAL:
                _record_denial(ctx, it, bad[0], bad[1])
        if (item["status"], item["policy_class"], item["blocked_reason"]) != new:
            if new[0] != item["status"]:
                backlog.transition(conn, item["id"], new[0], policy_class=new[1], blocked_reason=new[2])
            else:
                conn.execute("UPDATE backlog_items SET policy_class=%s, blocked_reason=%s, updated_at=now() "
                             "WHERE id=%s", (new[1], new[2], item["id"]))
        stats["ready" if new[0] == "READY" else "blocked"] += 1
    return stats


def _record_denial(ctx, it, tool, d):
    """정책이 막은 작업: Activity·감사에 남기고, 체크리스트 항목은 증적 없이 판정할 수 없음을 기록한다."""
    worker.record_invocation(ctx, it, None, 1, tool, d, "SKIPPED", error=d.reason)
    db.audit(ctx.conn, ctx.run["id"], "policy_denied", {"backlog_item": str(it["id"]), "tool": tool,
                                                         "decision": d.decision, "reason": d.reason})
    if it["kind"] == "checklist":
        worker.set_verdict(ctx.conn, it["run_checklist_item_id"], "INCONCLUSIVE",
                           f"정책 판정 {d.decision} 로 증적을 수집하지 못함: {d.reason}", actor="planner",
                           decided_by="rule")
    else:
        ctx.conn.execute("UPDATE exploration_nodes SET state='BLOCKED' WHERE id=%s", (it["exploration_node_id"],))


def plan(ctx) -> dict:
    """한 번의 계획 주기. 반환값은 통계이며, 새로 할 일이 없으면 0건을 만들고 끝난다."""
    conn, run_id = ctx.conn, ctx.run["id"]
    events = [r["kind"] for r in conn.execute(
        "UPDATE change_events SET processed_at=now() WHERE run_id=%s AND processed_at IS NULL RETURNING kind",
        (run_id,))]
    stats = {"events": len(events), "reclaimed": backlog.reclaim_expired(conn, run_id),
             "created": _propose_checklist_tasks(ctx), "explorations": 0}
    stats.update(_admit(ctx))
    conn.commit()
    # 탐색 제안은 새 판정/사실이 생겼고 체크리스트 작업이 더 남지 않았을 때만 시도한다
    queued = backlog.counts(conn, run_id)
    if ctx.llm and {"verdict", "fact"} & set(events) and not (queued.get("READY") or queued.get("RUNNING")):
        stats["explorations"] = explore.propose(ctx)
        if stats["explorations"]:
            stats.update(_admit(ctx))
        conn.commit()
    if any(stats[k] for k in ("created", "explorations", "reclaimed")):
        db.audit(conn, run_id, "planned", stats)
        conn.commit()
    return stats
