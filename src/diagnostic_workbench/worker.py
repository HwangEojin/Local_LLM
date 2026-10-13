"""Worker: 점유한 작업 하나를 실행한다. 도구 호출마다 정책 판정 -> 실행 -> 증적 -> Activity 기록.

체크리스트 작업의 판정은 기존 audit.verdict(결정론적 규칙)가 내린다. 탐색 작업은 증적과 사실(fact) 노드만
남기며 체크리스트 판정을 바꾸지 않는다.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from psycopg.types.json import Jsonb

from audit import schemas
from audit.checklist_engine import is_applicable, render_args
from audit.local_model import LLMError, build_analysis_messages
from audit.models import ToolError
from audit.policy import mask_secrets, redact, secret_values
from audit.verdict import decide, evaluate_rule

from . import backlog, db, evidence, policy

_LEGACY_TO_DB = {"MANUAL_REVIEW": "NEEDS_REVIEW"}


def load_item(ctx, item: dict) -> dict:
    """작업 행에 체크리스트 항목 원문과 대상(Target)을 붙인다."""
    it = dict(item)
    it["target"] = ctx.policy.targets[ctx.target_ids[item["asset_id"]]]
    if item["kind"] == "checklist":
        row = ctx.conn.execute(
            "SELECT ci.id AS checklist_item_id, ci.check_id, ci.body, rci.verdict FROM run_checklist_items rci "
            "JOIN checklist_items ci ON ci.id = rci.checklist_item_id WHERE rci.id = %s",
            (item["run_checklist_item_id"],)).fetchone()
        it.update(row)
    return it


def calls(it: dict) -> list[tuple[str, str, dict]]:
    """이 작업이 수행할 도구 호출 목록: (요구사항 id, 도구, 인자)."""
    if it["kind"] == "exploration":
        return [("explore", it["payload"]["tool"], it["payload"]["args"])]
    body = it["body"]
    if not is_applicable(body, it["target"]) or body["item_status"] != "complete":
        return []
    return [(r["id"], r["tool"], render_args(r["args"], it["target"])) for r in body["evidence_requirements"]]


def record_invocation(ctx, it, worker_run_id, step, tool, d: policy.Decision, status, *, started=None,
                      error=None, evidence_id=None, tool_version=""):
    ctx.conn.execute(
        "INSERT INTO tool_invocations (run_id, worker_run_id, backlog_item_id, asset_id, step_no, tool, "
        "tool_version, args, policy_decision, policy_reason, status, error, evidence_id, started_at, ended_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())",
        (ctx.run["id"], worker_run_id, it["id"], it["asset_id"], step, tool, tool_version, db.masked(d.args),
         d.decision, d.reason, status, mask_secrets(error) if error else None, evidence_id,
         started or datetime.now(timezone.utc)))


def set_verdict(conn, rci_id, verdict, reason, *, actor, decided_by, evidence_ids=(), rule_outcome=None,
                llm_outcome=None):
    old = conn.execute("SELECT verdict FROM run_checklist_items WHERE id=%s FOR UPDATE", (rci_id,)).fetchone()
    conn.execute(
        "UPDATE run_checklist_items SET verdict=%s, decided_by=%s, reason=%s, rule_outcome=%s, llm_outcome=%s, "
        "updated_at=now() WHERE id=%s",
        (verdict, decided_by, reason, Jsonb(rule_outcome) if rule_outcome else None,
         Jsonb(llm_outcome) if llm_outcome else None, rci_id))
    conn.execute(
        "INSERT INTO verdict_history (run_checklist_item_id, old_verdict, new_verdict, reason, actor, evidence_ids) "
        "VALUES (%s,%s,%s,%s,%s,%s)", (rci_id, old["verdict"], verdict, reason, actor, list(evidence_ids)))


def _tool_calls_used(ctx) -> int:
    return ctx.conn.execute("SELECT count(*) AS n FROM tool_invocations WHERE run_id=%s AND status <> 'SKIPPED'",
                            (ctx.run["id"],)).fetchone()["n"]


def run_tool(ctx, it, worker_run_id, step, req_id, tool, args):
    """(evidence_id, ToolOutput|None). 정책 거부·실패도 증적 행과 Activity 로 남긴다."""
    common = dict(run_id=ctx.run["id"], asset_id=it["asset_id"], requirement_id=req_id, tool=tool,
                  rci_id=it.get("run_checklist_item_id"), backlog_item_id=it["id"])
    d = policy.decide(ctx, tool, args, it["asset_id"])
    if d.decision == policy.ALLOW and _tool_calls_used(ctx) >= ctx.run["budget"]["max_tool_calls"]:
        d = policy.Decision(policy.DENY, "실행 예산 max_tool_calls 에 도달함", d.args)
    if d.decision != policy.ALLOW:
        record_invocation(ctx, it, worker_run_id, step, tool, d, "SKIPPED", error=d.reason)
        db.audit(ctx.conn, ctx.run["id"], "policy_denied", {"backlog_item": str(it["id"]), "tool": tool,
                                                             "decision": d.decision, "reason": d.reason})
        eid = evidence.store(ctx.conn, ctx.cfg.evidence_dir, args=d.args,
                             error=f"정책 거부({d.decision}): {d.reason}", **common)
        ctx.conn.commit()
        return eid, None
    spec, err = ctx.registry[tool], None
    for _ in range(2 if spec.risk == "read" else 1):  # 상태를 바꾸는 도구는 재시도하지 않는다
        wait = ctx.last_call + ctx.cfg.min_call_interval_ms / 1000 - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        ctx.last_call = time.monotonic()
        started = datetime.now(timezone.utc)
        try:
            out = spec.executor(d.args)
            if spec.post:
                out = spec.post(out, it["target"])
        except ToolError as e:
            err = str(e)
            record_invocation(ctx, it, worker_run_id, step, tool, d, "FAILED", started=started, error=err,
                              tool_version=spec.version)
            ctx.conn.commit()
            continue
        eid = evidence.store(ctx.conn, ctx.cfg.evidence_dir, args=d.args, output=out, **common)
        record_invocation(ctx, it, worker_run_id, step, tool, d, "SUCCEEDED", started=started, evidence_id=eid,
                          tool_version=out.tool_version)
        _observe_assets(ctx, it, tool, d.args, out, eid)
        ctx.conn.commit()
        return eid, out
    eid = evidence.store(ctx.conn, ctx.cfg.evidence_dir, args=d.args, error=mask_secrets(err),
                         tool_version=spec.version, **common)
    ctx.conn.commit()
    return eid, None


def _observe_assets(ctx, it, tool, args, out, evidence_id):
    """도구가 실제로 관찰한 자산과 관계를 자산 그래프에 등록한다(출처·실행·증적을 남긴다).

    ponytail: 구조가 확정된 fs.list_dir 출력만 다룬다. 다른 도구는 출력 형식을 실제로 확인한 뒤 추가한다.
    """
    if tool != "fs.list_dir" or out.truncated:
        return
    conn, run_id = ctx.conn, ctx.run["id"]

    def asset(value):
        return conn.execute(
            "INSERT INTO assets (kind, value, source, verification, first_seen_run_id) "
            "VALUES ('path', %s, %s, 'OBSERVED', %s) "
            "ON CONFLICT (kind, value) DO UPDATE SET value = EXCLUDED.value RETURNING id",
            (value, f"tool:{tool}", run_id)).fetchone()["id"]
    parent = asset(args["path"])
    for entry in json.loads(out.data)[:200]:
        conn.execute("INSERT INTO asset_edges (src_id, dst_id, rel, run_id, source) VALUES (%s,%s,'contains',%s,%s) "
                     "ON CONFLICT DO NOTHING",
                     (parent, asset(str(Path(args["path"]) / entry["name"])), run_id, f"evidence:{evidence_id}"))


def _analyze(ctx, body, target, ev: dict) -> dict:
    """LLM 참고 의견. 알려진 증적 id 를 인용하지 않으면 버린다. 판정을 확정하지 않는다."""
    msgs = build_analysis_messages(body, target, ev, ctx.cfg.settings.ollama["max_request_bytes"] // 2)
    try:
        out = ctx.llm.structured_chat(msgs, schemas.LLM_ANALYSIS, f"analyze:{body['check_id']}")
    except LLMError as e:
        db.audit(ctx.conn, ctx.run["id"], "llm_error", {"check_id": body["check_id"], "error": str(e)})
        return {"verdict": "ERROR", "error": str(e)}
    secrets: set[str] = set()
    for e in ev.values():
        secrets |= secret_values((e["data"] or b"").decode("utf-8", errors="replace"))
    out["rationale"] = redact(out["rationale"], secrets)
    if not out["evidence_ids"] or not set(out["evidence_ids"]) <= {e["evidence_id"] for e in ev.values()}:
        return {"verdict": "ERROR", "error": "LLM 이 증적 id 를 인용하지 않았거나 알 수 없는 id 를 인용함", "raw": out}
    return out


def _run_checklist(ctx, it, wr) -> tuple[str, str | None]:
    body, target = it["body"], it["target"]
    applicable = is_applicable(body, target)
    ev, ids, complete = {}, [], True
    for step, (req_id, tool, args) in enumerate(calls(it), 1):
        eid, out = run_tool(ctx, it, wr, step, req_id, tool, args)
        ids.append(eid)
        if out is None:
            complete = False
        else:
            ev[req_id] = {"evidence_id": str(eid), "data": out.data, "truncated": out.truncated}
    method = body["verification_method"]
    auto = applicable and complete and body["item_status"] == "complete"
    rule_outcome = evaluate_rule(method["rule"], ev) if auto and method["type"] == "rule" else None
    llm_outcome = _analyze(ctx, body, target, ev) \
        if ctx.llm and auto and ev and method["type"] in ("rule", "llm_assisted") else None
    verdict, _, reason = decide(body, applicable, complete, rule_outcome, llm_outcome)
    v = _LEGACY_TO_DB.get(verdict.value, verdict.value)
    set_verdict(ctx.conn, it["run_checklist_item_id"], v, reason, actor="worker", decided_by="rule",
                evidence_ids=ids, rule_outcome=rule_outcome, llm_outcome=llm_outcome)
    if v == "FAIL":  # 규칙과 증적이 뒷받침한 경우에만 발견 사항으로 올린다
        f = ctx.conn.execute(
            "INSERT INTO findings (run_id, asset_id, run_checklist_item_id, title, severity, rationale, "
            "remediation, verification) VALUES (%s,%s,%s,%s,%s,%s,%s,'RULE_CONFIRMED') "
            "ON CONFLICT (run_id, run_checklist_item_id) DO NOTHING RETURNING id",
            (ctx.run["id"], it["asset_id"], it["run_checklist_item_id"], body["title"], body["severity"],
             reason, body["remediation"])).fetchone()
        for eid in (rule_outcome["evidence_ids"] if f else []):
            ctx.conn.execute("INSERT INTO finding_evidence VALUES (%s,%s)", (f["id"], eid))
    db.notify_change(ctx.conn, ctx.run["id"], "verdict", it["run_checklist_item_id"])
    if v in ("PASS", "FAIL", "NOT_APPLICABLE"):
        return "SUCCEEDED", None
    if not complete:
        return "FAILED", reason
    return "NEEDS_REVIEW", None


def _run_exploration(ctx, it, wr) -> tuple[str, str | None]:
    (req_id, tool, args), = calls(it)
    eid, out = run_tool(ctx, it, wr, 1, req_id, tool, args)
    payload = {"tool": tool, "success": out is not None}
    if out is not None:  # 발췌는 다음 계획의 참고 자료다(신뢰할 수 없는 데이터, 비밀 값은 가린다)
        payload["excerpt"] = mask_secrets(out.data[:600].decode("utf-8", errors="replace"))
        payload["truncated"] = out.truncated or len(out.data) > 600
    fact = ctx.conn.execute(
        "INSERT INTO exploration_nodes (run_id, kind, state, origin, payload) VALUES (%s,'fact','RECORDED','worker',%s) "
        "RETURNING id", (ctx.run["id"], Jsonb(payload))).fetchone()["id"]
    ctx.conn.execute("INSERT INTO exploration_anchors (node_id, evidence_id) VALUES (%s,%s)", (fact, eid))
    ctx.conn.execute("INSERT INTO exploration_edges VALUES (%s,%s,'yields')", (it["exploration_node_id"], fact))
    ctx.conn.execute("UPDATE exploration_nodes SET state=%s WHERE id=%s",
                     ("DONE" if out is not None else "FAILED", it["exploration_node_id"]))
    db.notify_change(ctx.conn, ctx.run["id"], "fact", fact)
    return ("SUCCEEDED", None) if out is not None else ("FAILED", "도구 호출 실패 또는 정책 거부")


def execute(ctx, item: dict, worker_name: str) -> str:
    """점유한(RUNNING) 작업을 끝까지 처리하고 최종 작업 상태를 반환한다."""
    conn = ctx.conn
    wr = conn.execute("INSERT INTO worker_runs (run_id, backlog_item_id, worker) VALUES (%s,%s,%s) RETURNING id",
                      (ctx.run["id"], item["id"], worker_name)).fetchone()["id"]
    conn.commit()
    try:
        it = load_item(ctx, item)
        status, error = (_run_checklist if item["kind"] == "checklist" else _run_exploration)(ctx, it, wr)
    except Exception as e:  # 예상하지 못한 오류도 결과로 남긴다. 다음 작업은 계속된다
        conn.rollback()
        status, error = "FAILED", f"{type(e).__name__}: {e}"
        db.audit(conn, ctx.run["id"], "worker_error", {"backlog_item": str(item["id"]), "error": error})
    backlog.transition(conn, item["id"], status, last_error=mask_secrets(error) if error else None,
                       lease_owner=None, lease_until=None)
    conn.execute("UPDATE worker_runs SET ended_at=now(), outcome=%s, error=%s WHERE id=%s",
                 (status, mask_secrets(error) if error else None, wr))
    conn.commit()
    return status
