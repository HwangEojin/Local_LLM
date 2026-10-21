"""Worker Activity(도구 호출 단계) 검색. 과거 과정은 참고 자료일 뿐이며 현재 대상의 증적을 대신하지 않는다.

ponytail: ILIKE 텍스트 검색. 데이터가 커져 느려지면 tsvector/pg_trgm 인덱스를 추가한다.
"""
from __future__ import annotations

from audit.models import ConfigError

from . import db

_COLS = ("ti.id, ti.run_id, ti.backlog_item_id, ti.step_no, ti.tool, ti.tool_version, ti.args, "
         "ti.policy_decision, ti.policy_reason, ti.status, ti.error, ti.evidence_id, ti.started_at, ti.ended_at, "
         "a.kind AS asset_kind, a.value AS asset_value, b.kind AS task_kind, b.reason AS task_reason, "
         "e.media_type AS evidence_media_type, e.size AS evidence_size, e.success AS evidence_success")
_FROM = ("FROM tool_invocations ti LEFT JOIN assets a ON a.id = ti.asset_id "
         "LEFT JOIN backlog_items b ON b.id = ti.backlog_item_id "
         "LEFT JOIN evidence e ON e.id = ti.evidence_id ")


def search_all_worker_traces(conn, query: str = "", *, tool=None, status=None, exclude_run=None, limit=20):
    """도구 이름·인자·오류·작업 이유에서 query 를 찾는다. exclude_run 으로 현재 실행을 뺄 수 있다."""
    where, params = ["(%s = '' OR ti.tool ILIKE %s OR ti.args::text ILIKE %s OR ti.error ILIKE %s "
                     "OR b.reason ILIKE %s)"], [query] + [f"%{query}%"] * 4
    for col, val in (("ti.tool = %s", tool), ("ti.status = %s", status), ("ti.run_id <> %s", exclude_run)):
        if val is not None:
            where.append(col)
            params.append(val)
    return conn.execute(f"SELECT {_COLS} {_FROM} WHERE {' AND '.join(where)} ORDER BY ti.started_at DESC LIMIT %s",
                        (*params, limit)).fetchall()


def get_run_trace(conn, run_id, limit=100):
    """한 실행의 최근 Worker Activity(도구 호출 단계) — 웹 UI 모니터링 탭에서 쓴다."""
    return conn.execute(f"SELECT {_COLS} {_FROM} WHERE ti.run_id = %s ORDER BY ti.started_at DESC LIMIT %s",
                        (run_id, limit)).fetchall()


def get_worker_trace(conn, backlog_item_id, step_nos=None):
    """작업 하나의 단계별 상세."""
    return conn.execute(
        f"SELECT {_COLS} {_FROM} WHERE ti.backlog_item_id = %s AND (%s::int[] IS NULL OR ti.step_no = ANY(%s)) "
        "ORDER BY ti.started_at, ti.step_no", (backlog_item_id, step_nos, step_nos)).fetchall()


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
