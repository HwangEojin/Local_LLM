"""비동기 자산 보강: 워커의 작업과 독립적으로, 이 실행이 아는 host/url 자산에
읽기 전용 보강(DNS 조회/HTTP HEAD)을 한 번씩 자동으로 건다.

LLM 의 판단이 아니라 planner 가 매 계획 주기에 스스로 건다(enqueue 는 explore.propose 와 달리 LLM을
부르지 않는다). 그래도 모든 호출은 기존 정책 계층(policy.decide)과 Worker 파이프라인을 그대로 거친다
(CLAUDE.md §8 "모든 도구 호출을 단일 정책 검사 경로로 통과시킨다") — 새 읽기 전용 도구(net_tools)를
추가했을 뿐 새 실행 경로는 만들지 않았다.
"""
from __future__ import annotations

from psycopg.types.json import Jsonb

_TOOL_BY_KIND = {"host": "net.dns_resolve", "url": "net.http_head"}
_ARG_BY_KIND = {"host": "host", "url": "url"}


def enqueue(ctx) -> int:
    """이 실행이 아는 host/url 자산마다 보강 작업을 한 번씩 큐에 올린다. 이미 있으면 건너뛴다."""
    conn, run_id = ctx.conn, ctx.run["id"]
    declared = conn.execute(
        "SELECT a.id, a.kind, a.value FROM assets a JOIN run_assets ra ON ra.asset_id = a.id "
        "WHERE ra.run_id=%s AND a.kind IN ('host','url')", (run_id,)).fetchall()
    # 범위 선언 자산(run_assets)뿐 아니라 이 실행 중 도구가 실제로 관찰한 자산(asset_edges)도 보강한다.
    observed = conn.execute(
        "SELECT DISTINCT a.id, a.kind, a.value FROM assets a JOIN asset_edges e ON e.dst_id = a.id "
        "WHERE e.run_id=%s AND a.kind IN ('host','url')", (run_id,)).fetchall()
    by_id = {a["id"]: a for a in declared}
    for a in observed:
        by_id.setdefault(a["id"], a)
    if not by_id:
        return 0
    goal = conn.execute("SELECT id FROM exploration_nodes WHERE run_id=%s AND kind='goal' ORDER BY created_at LIMIT 1",
                        (run_id,)).fetchone()
    if goal is None:
        return 0
    accepted = 0
    for asset in by_id.values():
        tool = _TOOL_BY_KIND.get(asset["kind"])
        if tool is None or tool not in ctx.registry:
            continue
        args = {_ARG_BY_KIND[asset["kind"]]: asset["value"]}
        key = f"enrich:{asset['id']}:{tool}"
        if conn.execute("SELECT 1 FROM backlog_items WHERE run_id=%s AND idempotency_key=%s",
                        (run_id, key)).fetchone():
            continue
        node = conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'intent','enrich',%s) "
            "RETURNING id", (run_id, Jsonb({"tool": tool, "args": args}))).fetchone()["id"]
        conn.execute("INSERT INTO exploration_edges VALUES (%s,%s,'spawns')", (goal["id"], node))
        conn.execute("INSERT INTO exploration_anchors (node_id, asset_id) VALUES (%s,%s)", (node, asset["id"]))
        conn.execute(
            "INSERT INTO backlog_items (run_id, kind, exploration_node_id, asset_id, priority, idempotency_key, "
            "reason, payload, max_attempts) VALUES (%s,'exploration',%s,%s,90,%s,%s,%s,1) "
            "ON CONFLICT (run_id, idempotency_key) DO NOTHING",
            (run_id, node, asset["id"], key, f"자산 보강: {tool}({asset['value']})",
             Jsonb({"tool": tool, "args": args})))
        accepted += 1
    return accepted
