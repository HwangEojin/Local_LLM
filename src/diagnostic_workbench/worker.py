"""Worker: 점유한 작업 하나를 실행한다. 도구 호출마다 정책 판정 -> 실행 -> 증적 -> Activity 기록.

체크리스트 작업의 판정은 기존 audit.verdict(결정론적 규칙)가 내린다. 탐색 작업은 증적과 사실(fact) 노드만
남기며 체크리스트 판정을 바꾸지 않는다.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urljoin

from psycopg.types.json import Jsonb

from audit import schemas, signals
from audit.adapters import burp as burp_adapter
from audit.checklist_engine import is_applicable, render_args
from audit.local_model import LLMError, build_analysis_messages
from audit.models import AuditError, PolicyDenied, ToolError, ToolOutput
from audit.policy import mask_secrets, redact, secret_values
from audit.verdict import decide, evaluate_rule

from . import backlog, db, evidence, policy, tools

_LEGACY_TO_DB = {"MANUAL_REVIEW": "NEEDS_REVIEW"}
_LINK = re.compile(r"/url: (\S+)")  # Playwright 스냅샷의 링크 표기
# Burp 이력/Organizer/Scanner 결과 — 전부 adapters.burp.scope_filter(post) 가 같은 모양({"items": [...]})
# 으로 걸러준 뒤 증적에 저장되므로(범위 밖은 이미 제거됨), 같은 방식으로 자산을 추출한다
# (README §7 "Burp 이력 기반 자산 추출", 2026-10-19 구현).
_BURP_HISTORY_TOOLS = {"mcp:burp/get_proxy_http_history", "mcp:burp/get_proxy_http_history_regex",
                       "mcp:burp/get_organizer_items", "mcp:burp/get_scanner_issues"}


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


FINDING_STATUSES = ("CANDIDATE", "NEEDS_REVIEW", "CONFIRMED", "REJECTED", "INCONCLUSIVE", "NOT_TESTED")


def set_finding_status(conn, finding_id, status, reason, *, actor, evidence_ids=()):
    """사람의 Finding 검토 판정을 기록한다(CLAUDE.md §9/§10).

    기존 findings 행과 finding_evidence 는 지우지 않고 finding_review_history 에 이력만 추가한다.
    CONFIRMED 는 연결된 증거가 있어야만 허용한다 — LLM 이나 규칙의 주장만으로 확정하지 않는다(§9 확정 조건).
    """
    f = conn.execute("SELECT status FROM findings WHERE id=%s FOR UPDATE", (finding_id,)).fetchone()
    if f is None:
        raise AuditError(f"finding {finding_id} 를 찾을 수 없음")
    for eid in evidence_ids:
        conn.execute("INSERT INTO finding_evidence VALUES (%s,%s) ON CONFLICT DO NOTHING", (finding_id, eid))
    if status == "CONFIRMED":
        n = conn.execute(
            "SELECT count(*) AS n FROM finding_evidence WHERE finding_id=%s", (finding_id,)).fetchone()["n"]
        if n == 0:
            raise AuditError("CONFIRMED 에는 연결된 증거가 필요함(CLAUDE.md §9) — 증거 없이 확정할 수 없음. "
                             "먼저 증적을 연결하거나 NEEDS_REVIEW/INCONCLUSIVE 로 두세요")
    conn.execute(
        "UPDATE findings SET status=%s, reviewed_by=%s, reviewed_at=now(), review_reason=%s WHERE id=%s",
        (status, actor, reason, finding_id))
    conn.execute(
        "INSERT INTO finding_review_history (finding_id, old_status, new_status, reason, actor, evidence_ids) "
        "VALUES (%s,%s,%s,%s,%s,%s)", (finding_id, f["status"], status, reason, actor, list(evidence_ids)))


def create_retest(conn, run_id, finding_id, *, operator, reason) -> list:
    """사람이 Finding 의 추가 검증(재검증)을 요청한다(CLAUDE.md §10 "추가 검증 요청").

    그 finding 의 근거가 된 증거를 낸 도구·인자를 그대로 새 탐색 작업으로 백로그에 다시 올린다. 상태는
    바꾸지 않는다 — 재검증 결과가 나오면 사람이 finding review 로 다시 판정해야 한다. 요청 사실은
    finding_review_history 에 남는다(old_status == new_status, 판정 변경이 아니라 요청 기록).
    """
    f = conn.execute("SELECT status FROM findings WHERE id=%s", (finding_id,)).fetchone()
    if f is None:
        raise AuditError(f"finding {finding_id} 를 찾을 수 없음")
    calls = conn.execute(
        "SELECT DISTINCT e.asset_id, e.tool, e.args FROM finding_evidence fe "
        "JOIN evidence e ON e.id = fe.evidence_id WHERE fe.finding_id=%s", (finding_id,)).fetchall()
    if not calls:
        raise AuditError("이 finding 에는 재실행할 증거(도구/인자)가 없음 — 재검증을 요청할 수 없음")
    goal = conn.execute("SELECT id FROM exploration_nodes WHERE run_id=%s AND kind='goal' ORDER BY created_at LIMIT 1",
                        (run_id,)).fetchone()["id"]
    item_ids = []
    for c in calls:
        # 탐색 신호(signals.py)로 확정된 finding 이면 재검증 완료 시 같은 신호로 자동 판정할 수 있게
        # 취약점 클래스를 지금(원래 인자 기준) 분류해 둔다. 모르면 None — 그 경우 자동 판정은 생략되고
        # 사람이 직접 검토한다(체크리스트 규칙·LLM 확정 finding 은 재현할 결정론적 신호가 없음).
        node = conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'intent','retest',%s) "
            "RETURNING id", (run_id, Jsonb({"tool": c["tool"], "args": c["args"],
                                            "retest_of_finding": str(finding_id),
                                            "retest_vuln_class": signals.classify_payload(c["args"])}))).fetchone()["id"]
        conn.execute("INSERT INTO exploration_edges VALUES (%s,%s,'spawns')", (goal, node))
        conn.execute("INSERT INTO exploration_anchors (node_id, asset_id) VALUES (%s,%s)", (node, c["asset_id"]))
        item = conn.execute(
            "INSERT INTO backlog_items (run_id, kind, exploration_node_id, asset_id, priority, idempotency_key, "
            "reason, payload, max_attempts) VALUES (%s,'exploration',%s,%s,70,%s,%s,%s,1) RETURNING id",
            (run_id, node, c["asset_id"], f"retest:{finding_id}:{node}",
             f"재검증 요청(operator={operator}): {reason}"[:300],
             Jsonb({"tool": c["tool"], "args": c["args"]}))).fetchone()["id"]
        item_ids.append(item)
    conn.execute(
        "INSERT INTO finding_review_history (finding_id, old_status, new_status, reason, actor, evidence_ids) "
        "VALUES (%s,%s,%s,%s,%s,'{}')",
        (finding_id, f["status"], f["status"], f"재검증 요청됨: {reason} (새 탐색 작업 {len(item_ids)}건)",
         f"reviewer:{operator}"))
    return item_ids


def create_llm_finding(conn, run_id, asset_id, *, title, severity, rationale, remediation, evidence_ids, model):
    """LLM이 자신이 인용한 실존 증거를 근거로 finding을 직접 CONFIRMED로 기록한다(CLAUDE.md §1/§7/§9).
    인용한 증거 id가 이 실행(run)에 실제로 존재하는지만 확인하는
    최소한의 데이터 무결성 검사이고, 주장(취약점이 실제로 있는지)의 실체는 검증하지 않는다 — 그게 이
    정책의 요지다. finding_review_history 에 actor='llm:<model>' 로 남아 추적 가능하고, 사람은 이후에도
    finding review 로 재검토·기각할 수 있다(그 이력도 보존됨).

    인용한 증거가 하나도 실존하지 않으면 아무것도 기록하지 않고 None 을 돌려준다.
    """
    real = sorted({r["id"] for r in conn.execute(
        "SELECT id FROM evidence WHERE run_id=%s AND id = ANY(%s::uuid[])", (run_id, list(evidence_ids))).fetchall()})
    if not real:
        return None
    actor = f"llm:{model}"
    f = conn.execute(
        "INSERT INTO findings (run_id, asset_id, title, severity, rationale, remediation, verification, status, "
        "reviewed_by, reviewed_at, review_reason) "
        "VALUES (%s,%s,%s,%s,%s,%s,'LLM_CONFIRMED','CONFIRMED',%s,now(),%s) RETURNING id",
        (run_id, asset_id, title, severity, rationale, remediation, actor, rationale)).fetchone()
    for eid in real:
        conn.execute("INSERT INTO finding_evidence VALUES (%s,%s)", (f["id"], eid))
    conn.execute(
        "INSERT INTO finding_review_history (finding_id, old_status, new_status, reason, actor, evidence_ids) "
        "VALUES (%s,NULL,'CONFIRMED',%s,%s,%s)", (f["id"], rationale, actor, real))
    return f["id"]


def _tool_calls_used(ctx, kind: str) -> int:
    """kind('checklist'|'exploration')별로 따로 센다 — 탐색이 체크리스트 예산을 먹거나 그 반대가 되지
    않게 한다(CLAUDE.md §2 "각자의 예산... 따로 관리").
    """
    return ctx.conn.execute(
        "SELECT count(*) AS n FROM tool_invocations ti JOIN backlog_items b ON b.id = ti.backlog_item_id "
        "WHERE ti.run_id=%s AND ti.status <> 'SKIPPED' AND b.kind=%s", (ctx.run["id"], kind)).fetchone()["n"]


_BUDGET_KEY = {"checklist": "max_tool_calls"}  # 자율 탐색(exploration)에는 하드 리밋을 두지 않음(CLAUDE.md §1/§5.4/§8)


def run_tool(ctx, it, worker_run_id, step, req_id, tool, args):
    """(evidence_id, ToolOutput|None). 정책 거부·실패도 증적 행과 Activity 로 남긴다."""
    common = dict(run_id=ctx.run["id"], asset_id=it["asset_id"], requirement_id=req_id, tool=tool,
                  rci_id=it.get("run_checklist_item_id"), backlog_item_id=it["id"])
    d = policy.decide(ctx, tool, args, it["asset_id"])
    budget_key = _BUDGET_KEY.get(it["kind"])  # exploration 은 예산 키가 없음 — 벽시계 타임아웃으로만 끝낸다
    if budget_key and d.decision == policy.ALLOW and _tool_calls_used(ctx, it["kind"]) >= ctx.run["budget"][budget_key]:
        d = policy.Decision(policy.DENY, f"실행 예산 {budget_key} 에 도달함", d.args)
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
        escaped = policy.landed_out_of_scope(ctx, tool, out.data, it["asset_id"])
        if escaped:  # 범위 밖 페이지의 내용은 증적으로 남기지 않는다. 재시도하지 않는다
            record_invocation(ctx, it, worker_run_id, step, tool, d, "FAILED", started=started, error=escaped,
                              tool_version=out.tool_version)
            db.audit(ctx.conn, ctx.run["id"], "scope_violation", {"backlog_item": str(it["id"]), "tool": tool,
                                                                   "reason": escaped})
            eid = evidence.store(ctx.conn, ctx.cfg.evidence_dir, args=d.args, error=f"범위 이탈: {escaped}",
                                 tool_version=out.tool_version, **common)
            ctx.conn.commit()
            return eid, None
        is_mcp = tool.startswith("mcp:")
        media, ext = (("application/json", "json") if is_mcp
                     else ("application/xml", "xml") if tool == "nmap.scan"
                     else ("text/plain", "txt") if tool == "nmap.ssl_scan"
                     else ("application/octet-stream", "bin"))
        eid = evidence.store(ctx.conn, ctx.cfg.evidence_dir, args=d.args, output=out, media_type=media, ext=ext,
                             **common)
        if is_mcp and not out.truncated:  # 응답에 든 이미지(스크린샷)는 원본과 연결된 파생 증적으로 따로 둔다
            for mime, ext, raw in evidence.mcp_images(out.data):
                evidence.store(ctx.conn, ctx.cfg.evidence_dir, args=d.args, media_type=mime, ext=ext, derived_from=eid,
                               output=ToolOutput(raw, out.tool_version), **{**common, "requirement_id": f"{req_id}:image"})
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

    ponytail: 출력 형식을 실제로 확인한 fs.list_dir, nmap.scan, Playwright MCP 만 다룬다. 다른 도구는 확인 후 추가한다.
    """
    if out.truncated:
        return
    conn, run_id = ctx.conn, ctx.run["id"]

    def asset(kind, value):
        return conn.execute(
            "INSERT INTO assets (kind, value, source, verification, first_seen_run_id) "
            "VALUES (%s, %s, %s, 'OBSERVED', %s) "
            "ON CONFLICT (kind, value) DO UPDATE SET value = EXCLUDED.value RETURNING id",
            (kind, value, f"tool:{tool}", run_id)).fetchone()["id"]

    def edge(src, rel, dst):
        if src != dst:
            conn.execute("INSERT INTO asset_edges (src_id, dst_id, rel, run_id, source) VALUES (%s,%s,%s,%s,%s) "
                         "ON CONFLICT DO NOTHING", (src, dst, rel, run_id, f"evidence:{evidence_id}"))
    if tool == "fs.list_dir":
        parent = asset("path", args["path"])
        for entry in json.loads(out.data)[:200]:
            edge(parent, "contains", asset("path", str(Path(args["path"]) / entry["name"])))
    elif tool == "nmap.scan":  # 열린 포트만 서비스 자산으로 등록한다(제품·버전은 증적에 있다)
        for p in tools.parse_nmap_xml(out.data)[:200]:
            edge(it["asset_id"], "exposes", asset("service", f"{args['host']}:{p['port']}/{p['proto']}"))
    elif tool.startswith("mcp:playwright/"):
        text = policy._texts(out.data)
        pages = policy._PAGE_URL.findall(text)
        if not pages or pages[-1].startswith("about:"):
            return
        page = asset("url", urldefrag(pages[-1]).url)  # 도착 주소는 run_tool 이 이미 범위 안임을 확인했다
        edge(it["asset_id"], "serves", page)
        for href in _LINK.findall(text)[:200]:
            link = urldefrag(urljoin(pages[-1], href)).url
            try:  # 범위 밖 링크는 자산으로 등록하지 않는다
                ctx.policy.check_url(link, it["target"])
            except PolicyDenied:
                continue
            edge(page, "links_to", asset("url", link))
    elif tool in _BURP_HISTORY_TOOLS:
        try:
            items = json.loads(out.data).get("items", [])
        except ValueError:
            return
        for raw in items:
            for url in burp_adapter.request_urls(raw):
                url = urldefrag(url).url
                try:  # scope_filter 가 호스트 기준으로 이미 걸렀지만, 전체 URL 기준으로 다시 확인한다
                    ctx.policy.check_url(url, it["target"])
                except PolicyDenied:
                    continue
                edge(it["asset_id"], "serves", asset("url", url))


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
    if body.get("overlay"):  # 오버레이로 낸 판정은 원문 기준과 섞어 표시하지 않는다(CLAUDE.md §7A)
        reason = f"{reason} [{body['overlay']['note']}: {body['overlay']['basis']}]"
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


def _find_boolean_sibling(ctx, asset_id, exclude_evidence_id, want_classification, key):
    """같은 자산에 대한 이전 증적 중, 반대 참/거짓 조건이면서 나머지(pairing_key)가 같은 것을 찾는다.

    같은 주입 지점을 겨눈 짝인지 확인하는 것이 목적이다 — 다른 엔드포인트·다른 매개변수끼리 비교하면
    응답이 다른 게 당연해서 차등 신호가 아무 의미가 없다.
    """
    rows = ctx.conn.execute(
        "SELECT id, args, rel_path FROM evidence WHERE run_id=%s AND asset_id=%s AND success AND id <> %s "
        "ORDER BY collected_at DESC LIMIT 50", (ctx.run["id"], asset_id, exclude_evidence_id)).fetchall()
    for row in rows:
        if (signals.classify_sqli_boolean(row["args"]) == want_classification
                and signals.pairing_key(row["args"]) == key):
            return row
    return None


def _check_sqli_boolean_diff(ctx, it, evidence_id, args, data: bytes):
    """불린 기반 SQLi 차등 검증: 참/거짓 조건 짝을 찾아 응답이 의미 있게 달라지는지 본다(CLAUDE.md §7B,
    SQL 오류 문구 없이도 확정할 수 있는 유일한 SQLi 경로). 짝을 못 찾으면 None(아직 판단 대상 아님).
    """
    classification = signals.classify_sqli_boolean(args)
    key = signals.pairing_key(args)
    if classification is None or key is None:
        return None
    want = "FALSE" if classification == "TRUE" else "TRUE"
    sibling = _find_boolean_sibling(ctx, it["asset_id"], evidence_id, want, key)
    if sibling is None:
        return None
    sibling_data = evidence.read_bytes(sibling, ctx.cfg.evidence_dir)
    if sibling_data is None:
        return None
    true_body, false_body = (data, sibling_data) if classification == "TRUE" else (sibling_data, data)
    outcome = signals.verify_sqli_boolean_diff(true_body, false_body)
    return [evidence_id, sibling["id"]], outcome


def _create_finding(ctx, it, fact_id, evidence_ids, vclass, outcome):
    f = ctx.conn.execute(
        "INSERT INTO findings (run_id, asset_id, title, severity, rationale, remediation, verification) "
        "VALUES (%s,%s,%s,%s,%s,%s,'RULE_CONFIRMED') RETURNING id",
        (ctx.run["id"], it["asset_id"], f"탐색 기반 발견: {vclass}", signals.SEVERITY[vclass], outcome.reason,
         signals.REMEDIATION[vclass])).fetchone()
    for eid in evidence_ids:
        ctx.conn.execute("INSERT INTO finding_evidence VALUES (%s,%s)", (f["id"], eid))
    # exploration_edges 는 양쪽 다 exploration_nodes 를 참조하므로(findings.id 를 직접 못 넣음) finding 을
    # 가리키는 전용 노드를 만들고, 그 노드만 exploration_anchors.finding_id 로 실제 findings 행과 연결한다.
    finding_node = ctx.conn.execute(
        "INSERT INTO exploration_nodes (run_id, kind, state, origin, payload) VALUES "
        "(%s,'finding','RECORDED','worker',%s) RETURNING id",
        (ctx.run["id"], Jsonb({"vuln_class": vclass, "finding_id": str(f["id"])}))).fetchone()["id"]
    ctx.conn.execute("INSERT INTO exploration_anchors (node_id, finding_id) VALUES (%s,%s)", (finding_node, f["id"]))
    ctx.conn.execute("INSERT INTO exploration_edges VALUES (%s,%s,'proves')", (fact_id, finding_node))


def _promote_exploration_finding(ctx, it, fact_id, evidence_id, args, data: bytes):
    """탐색 fact 가 특정 취약점 클래스의 검증 신호와 일치하면 RULE_CONFIRMED finding 으로 승격한다(CLAUDE.md §7B).

    LLM 의견이 아니라 audit.signals 의 결정론적 checker 로만 승격한다. 일치하지 않으면 사실로만 남는다 —
    체크리스트 판정은 바꾸지 않으며, 체크리스트 커버리지에 반영하려면 'trace tag' 로 사람이 따로 매핑해야 한다.
    """
    diff = _check_sqli_boolean_diff(ctx, it, evidence_id, args, data)
    if diff is not None:
        evidence_ids, outcome = diff
        db.audit(ctx.conn, ctx.run["id"], "exploration_signal_checked",
                 {"fact": str(fact_id), "vuln_class": "SQLI", "matched": outcome.matched, "reason": outcome.reason,
                  "method": "boolean_diff"})
        if outcome.matched:
            _create_finding(ctx, it, fact_id, evidence_ids, "SQLI", outcome)
            return
    result = signals.check(args, data)
    if result is None:
        return
    vclass, outcome = result
    db.audit(ctx.conn, ctx.run["id"], "exploration_signal_checked",
             {"fact": str(fact_id), "vuln_class": vclass, "matched": outcome.matched, "reason": outcome.reason})
    if not outcome.matched:
        return
    _create_finding(ctx, it, fact_id, [evidence_id], vclass, outcome)


def _resolve_retest(ctx, it, eid, args, out) -> None:
    """재검증 작업(worker.create_retest 가 만든 intent)이면 원래 신호와 다시 대조해 REPRODUCED/FIXED/
    INCONCLUSIVE 를 finding_review_history 에 기록한다(CLAUDE.md §9 "확정할 수 있는 범위는 명확히
    정의"). 판정 가능한 범위는 딱 하나다 — 탐색 신호(audit.signals)로 확정됐던 finding 뿐이다. 체크리스트
    규칙이나 LLM 이 확정한 finding 은 재현할 결정론적 신호가 없어 그냥 넘어간다(사람이 직접 검토).

    findings.status 는 바꾸지 않는다 — "같은 신호가 다시 안 뜬다"는 "고쳐졌다"의 증거로 충분하지 않을 수
    있어(§5.4), 최종 판단은 여전히 사람이 finding review 로 한다. 이 함수는 그 판단에 필요한 재현 증거만
    이력에 남긴다.
    """
    intent = ctx.conn.execute("SELECT payload FROM exploration_nodes WHERE id=%s",
                             (it["exploration_node_id"],)).fetchone()
    payload = intent["payload"] if intent else {}
    finding_id = payload.get("retest_of_finding")
    if not finding_id:
        return
    f = ctx.conn.execute("SELECT status FROM findings WHERE id=%s", (finding_id,)).fetchone()
    if f is None:
        return
    vclass = payload.get("retest_vuln_class")
    evidence_ids = []
    if vclass is None or vclass not in signals.SIGNALS:
        verdict = "INCONCLUSIVE"
        detail = "결정론적으로 재확인할 신호가 없는 finding(체크리스트 규칙 또는 LLM 확정) — 사람이 직접 검토해야 함"
    elif out is None:
        verdict = "INCONCLUSIVE"
        detail = "도구 실행 실패 또는 정책 거부로 재현 여부를 확인할 수 없음(수정됐다는 증거 아님)"
    else:
        outcome = signals.SIGNALS[vclass](args, out.data)
        verdict, detail = ("REPRODUCED" if outcome.matched else "FIXED"), outcome.reason
        evidence_ids = [eid]
    ctx.conn.execute(
        "INSERT INTO finding_review_history (finding_id, old_status, new_status, reason, actor, evidence_ids) "
        "VALUES (%s,%s,%s,%s,'system:retest',%s)",
        (finding_id, f["status"], f["status"], f"재검증 결과: {verdict} — {detail}", evidence_ids))
    if evidence_ids:
        ctx.conn.execute("INSERT INTO finding_evidence VALUES (%s,%s) ON CONFLICT DO NOTHING", (finding_id, eid))


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
    if out is not None:
        _promote_exploration_finding(ctx, it, fact, eid, args, out.data)
    _resolve_retest(ctx, it, eid, args, out)
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
