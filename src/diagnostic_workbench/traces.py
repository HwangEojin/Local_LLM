"""Worker Activity(도구 호출 단계) 검색. 과거 과정은 참고 자료일 뿐이며 현재 대상의 증적을 대신하지 않는다.

ponytail: ILIKE 텍스트 검색. 데이터가 커져 느려지면 tsvector/pg_trgm 인덱스를 추가한다.
"""
from __future__ import annotations

from audit.models import ConfigError

from . import db

_COLS = ("ti.id, ti.run_id, ti.backlog_item_id, ti.step_no, ti.tool, ti.tool_version, ti.args, "
         "ti.policy_decision, ti.policy_reason, ti.status, ti.error, ti.outcome_class, ti.evidence_id, "
         "ti.started_at, ti.ended_at, "
         "a.kind AS asset_kind, a.value AS asset_value, b.kind AS task_kind, b.reason AS task_reason, "
         "e.media_type AS evidence_media_type, e.size AS evidence_size, e.success AS evidence_success")
_FROM = ("FROM tool_invocations ti LEFT JOIN assets a ON a.id = ti.asset_id "
         "LEFT JOIN backlog_items b ON b.id = ti.backlog_item_id "
         "LEFT JOIN evidence e ON e.id = ti.evidence_id ")


def _where(query: str, tool, status, exclude_run, run):
    where, params = ["(%s = '' OR ti.tool ILIKE %s OR ti.args::text ILIKE %s OR ti.error ILIKE %s "
                     "OR b.reason ILIKE %s)"], [query] + [f"%{query}%"] * 4
    for col, val in (("ti.tool = %s", tool), ("ti.status = %s", status), ("ti.run_id <> %s", exclude_run),
                     ("ti.run_id = %s", run)):
        if val is not None:
            where.append(col)
            params.append(val)
    return where, params


def search_all_worker_traces(conn, query: str = "", *, tool=None, status=None, exclude_run=None, run=None,
                             limit=20, offset=0):
    """도구 이름·인자·오류·작업 이유에서 query 를 찾는다. exclude_run 으로 현재 실행을 뺄 수 있고, run 으로 그
    실행 하나만 볼 수 있다(웹 '명령' 화면이 run 없이 전체 실행을 가로질러 쓴다)."""
    where, params = _where(query, tool, status, exclude_run, run)
    return conn.execute(
        f"SELECT {_COLS}, r.run_code {_FROM} LEFT JOIN runs r ON r.id = ti.run_id "
        f"WHERE {' AND '.join(where)} ORDER BY ti.started_at DESC LIMIT %s OFFSET %s",
        (*params, limit, offset)).fetchall()


def count_all_worker_traces(conn, query: str = "", *, tool=None, status=None, exclude_run=None, run=None) -> int:
    where, params = _where(query, tool, status, exclude_run, run)
    return conn.execute(f"SELECT count(*) AS n {_FROM} WHERE {' AND '.join(where)}", params).fetchone()["n"]


def tool_stats(conn, limit=50) -> list[dict]:
    """도구별 호출·실패 집계(웹 '명령' 화면 상단 요약)."""
    return conn.execute(
        "SELECT tool, count(*) AS calls, count(*) FILTER (WHERE status='FAILED') AS failures, "
        "max(started_at) AS last_at FROM tool_invocations GROUP BY tool ORDER BY calls DESC LIMIT %s",
        (limit,)).fetchall()


def get_run_trace(conn, run_id, limit=100):
    """한 실행의 최근 Worker Activity(도구 호출 단계) — 웹 UI 모니터링 탭에서 쓴다."""
    return conn.execute(f"SELECT {_COLS} {_FROM} WHERE ti.run_id = %s ORDER BY ti.started_at DESC LIMIT %s",
                        (run_id, limit)).fetchall()


def get_worker_trace(conn, backlog_item_id, step_nos=None):
    """작업 하나의 단계별 상세."""
    return conn.execute(
        f"SELECT {_COLS} {_FROM} WHERE ti.backlog_item_id = %s AND (%s::int[] IS NULL OR ti.step_no = ANY(%s)) "
        "ORDER BY ti.started_at, ti.step_no", (backlog_item_id, step_nos, step_nos)).fetchall()


_ORIGIN_LABEL = {"operator": "운영자", "llm": "LLM 제안", "trigger": "트리거", "enrich": "자동 보강",
                 "worker": "실행 도구", "retest": "재검증 요청"}


def _node_entry(node) -> dict:
    """탐색 노드 하나를 체인 표시용으로 요약한다. origin='llm' 인 노드의 rationale 은 LLM 의 주장일 뿐이라
    inferred, 그 외(운영자 목표·트리거 조건·자동 보강·실행 도구 결과)는 observed(CLAUDE.md §3/§9)."""
    payload = node["payload"] or {}
    inferred = node["origin"] == "llm"
    # origin=llm 인 노드는 rationale 을 summary 가 아니라 별도 "rationale" 필드로만 보여 준다(중복 표시 방지) —
    # summary 는 어떤 도구를 왜 골랐는지가 아니라 무엇을 했는지(관찰 가능한 사실)를 나타낸다.
    summary = payload.get("text") or payload.get("trigger_name") or payload.get("tool") or ""
    entry = {"id": node["id"], "kind": node["kind"], "state": node["state"], "origin": node["origin"],
             "origin_label": _ORIGIN_LABEL.get(node["origin"], node["origin"]), "summary": summary,
             "created_at": node["created_at"], "node_kind": "inferred" if inferred else "observed"}
    if inferred and payload.get("rationale"):
        entry["rationale"] = payload["rationale"]
    return entry


def get_evidence_provenance(conn, run_id, evidence_id) -> dict | None:
    """증거 하나의 출처 체인(G5-a, UPGRADE_PLAN.md): evidence ← tool_invocation ← backlog_item
    ← exploration_node(들, goal 까지 거슬러 올라감) → finding_evidence → findings(+ G2 투표).

    탐색/트리거/자동보강/재검증 경로 모두 run_tool()이 tool_invocations.backlog_item_id 를 늘 채우고,
    그 backlog_item.exploration_node_id 가 작업을 낳은 intent 노드를 직접 가리킨다 — worker._run_exploration
    이 따로 만드는 fact 노드도 같은 backlog_item 을 거치므로 별도 처리가 필요 없다. 프록시로 수집된 트래픽
    (backlog_item_id 없음)처럼 탐색 결정과 무관한 증거는 exploration_path 가 빈 목록으로 돌아온다."""
    evidence = conn.execute("SELECT * FROM evidence WHERE id=%s AND run_id=%s", (evidence_id, run_id)).fetchone()
    if evidence is None:
        return None
    ti = conn.execute(
        "SELECT tool, tool_version, args, policy_decision, policy_reason, status, error, backlog_item_id "
        "FROM tool_invocations WHERE evidence_id=%s ORDER BY started_at LIMIT 1", (evidence_id,)).fetchone()

    backlog_item_id = ti["backlog_item_id"] if ti else evidence["backlog_item_id"]
    backlog_item = None
    node_id = None
    if backlog_item_id:
        backlog_item = conn.execute(
            "SELECT id, kind, status, reason, exploration_node_id FROM backlog_items WHERE id=%s",
            (backlog_item_id,)).fetchone()
        if backlog_item:
            node_id = backlog_item["exploration_node_id"]

    path = []
    seen = set()
    current = node_id
    for _ in range(10):  # 실제로는 goal 까지 늘 1홉 — 방어적으로 상한만 둠, 끝없이 돌지 않게
        if current is None or current in seen:
            break
        seen.add(current)
        node = conn.execute("SELECT id, kind, state, origin, payload, created_at FROM exploration_nodes "
                            "WHERE id=%s", (current,)).fetchone()
        if node is None:
            break
        path.append(_node_entry(node))
        if node["kind"] == "goal":
            break
        parent = conn.execute("SELECT src_id FROM exploration_edges WHERE dst_id=%s AND rel='spawns' LIMIT 1",
                              (current,)).fetchone()
        current = parent["src_id"] if parent else None
    path.reverse()

    findings = conn.execute(
        "SELECT f.* FROM finding_evidence fe JOIN findings f ON f.id = fe.finding_id "
        "WHERE fe.evidence_id=%s ORDER BY f.created_at", (evidence_id,)).fetchall()
    finding_out = []
    for f in findings:
        votes = conn.execute(
            "SELECT model, stage, verdict, rationale, created_at FROM finding_llm_votes "
            "WHERE finding_id=%s ORDER BY id", (f["id"],)).fetchall()
        inferred = f["verification"] in ("LLM_PROPOSED", "LLM_CONFIRMED")
        finding_out.append({**f, "node_kind": "inferred" if inferred else "observed",
                            "llm_votes": [{**v, "node_kind": "inferred"} for v in votes]})

    return {
        "evidence": {**evidence, "node_kind": "observed"},
        "tool_invocation": {**ti, "node_kind": "observed"} if ti else None,
        "backlog_item": {**backlog_item, "node_kind": "observed"} if backlog_item else None,
        "exploration_path": path,
        "findings": finding_out,
    }


def tag_node(conn, run, node_ref: str, check_id: str) -> dict:
    """탐색 노드(보통 검증된 fact 를 낳은 intent)에 체크리스트 check_id 를 사후 매핑한다(CLAUDE.md §7B.3).

    이 태그는 탐색이 뭘 할지 정하지 않는다 — 이미 검증된 발견을 체크리스트 커버리지에 반영하기 위한
    사후 메타데이터일 뿐이며, 공식 판정(run_checklist_items.verdict)을 바꾸지 않는다. 사람(또는 사람이
    검토한 제안)만 호출한다 — 제목 유사도 등으로 자동 매핑하지 않는다.
    """
    node = conn.execute("SELECT id FROM exploration_nodes WHERE run_id=%s AND id::text LIKE %s",
                        (run["id"], f"{node_ref}%")).fetchone()
    if not node:
        raise ConfigError(f"이 실행에 없는 탐색 노드: {node_ref}")
    item = conn.execute("SELECT id FROM checklist_items WHERE checklist_version_id=%s AND check_id=%s",
                        (run["checklist_version_id"], check_id)).fetchone()
    if not item:
        raise ConfigError(f"이 실행의 체크리스트에 없는 check_id: {check_id}")
    existing = conn.execute("SELECT 1 FROM exploration_anchors WHERE node_id=%s AND checklist_item_id=%s",
                            (node["id"], item["id"])).fetchone()
    if not existing:
        conn.execute("INSERT INTO exploration_anchors (node_id, checklist_item_id) VALUES (%s,%s)",
                    (node["id"], item["id"]))
    db.audit(conn, run["id"], "exploration_mapped_to_checklist",
             {"node_id": str(node["id"]), "check_id": check_id, "already_tagged": bool(existing)})
    return {"node_id": node["id"], "checklist_item_id": item["id"]}
