"""Worker Activity(도구 호출 단계) 검색. 과거 과정은 참고 자료일 뿐이며 현재 대상의 증적을 대신하지 않는다.

ponytail: ILIKE 텍스트 검색. 데이터가 커져 느려지면 tsvector/pg_trgm 인덱스를 추가한다.
"""
from __future__ import annotations

_COLS = ("ti.id, ti.run_id, ti.backlog_item_id, ti.step_no, ti.tool, ti.tool_version, ti.args, "
         "ti.policy_decision, ti.policy_reason, ti.status, ti.error, ti.evidence_id, ti.started_at, ti.ended_at, "
         "a.kind AS asset_kind, a.value AS asset_value, b.kind AS task_kind, b.reason AS task_reason")
_FROM = ("FROM tool_invocations ti LEFT JOIN assets a ON a.id = ti.asset_id "
         "LEFT JOIN backlog_items b ON b.id = ti.backlog_item_id ")


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


def get_worker_trace(conn, backlog_item_id, step_nos=None):
    """작업 하나의 단계별 상세."""
    return conn.execute(
        f"SELECT {_COLS} {_FROM} WHERE ti.backlog_item_id = %s AND (%s::int[] IS NULL OR ti.step_no = ANY(%s)) "
        "ORDER BY ti.started_at, ti.step_no", (backlog_item_id, step_nos, step_nos)).fetchall()
