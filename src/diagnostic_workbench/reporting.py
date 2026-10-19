"""보고서: 기존 audit.report / audit.pentest_report 를 수정 없이 재사용한다.

LegacyStorage 가 PostgreSQL 기록을 기존 모듈이 읽는 형식(SQLite Storage 의 조회 메서드)으로 보여 준다.
기존 양식에는 '미실행' 판정이 없어 NOT_RUN 은 진단불가(INCONCLUSIVE)로 싣고 사유에 미실행임을 적으며,
실제 커버리지(NOT_RUN 포함)·작업 상태·차단 사유·도구 실패는 Excel 의 '커버리지' 시트에 따로 남긴다.
"""
from __future__ import annotations

import json
from pathlib import Path

from audit.checklist_engine import Checklists
from audit.evidence import EvidenceStore
from audit.models import AuditError
from audit.policy import mask_secrets

from . import db

_TO_LEGACY = {"NEEDS_REVIEW": "MANUAL_REVIEW", "NOT_RUN": "INCONCLUSIVE"}
_RESULTS = (
    "SELECT rci.id, rci.run_id::text AS run_id, ci.check_id, ra.target_id, rci.verdict, rci.decided_by, rci.reason, "
    "rci.rule_outcome, rci.llm_outcome, rci.updated_at, ci.body, cv.checklist_id, cv.version AS checklist_version, "
    "COALESCE((SELECT array_agg(e.id::text ORDER BY e.collected_at) FROM evidence e "
    "          WHERE e.run_checklist_item_id = rci.id), '{}') AS evidence_ids "
    "FROM run_checklist_items rci JOIN checklist_items ci ON ci.id = rci.checklist_item_id "
    "JOIN checklist_versions cv ON cv.id = ci.checklist_version_id "
    "JOIN run_assets ra ON ra.run_id = rci.run_id AND ra.asset_id = rci.asset_id ")
_EVIDENCE = (
    "SELECT e.id::text AS evidence_id, e.run_id::text AS run_id, COALESCE(ci.check_id, '(탐색)') AS check_id, "
    "ra.target_id, e.requirement_id, e.collected_at, a.kind || ':' || a.value AS target_ref, e.tool, e.tool_version, "
    "e.args, e.rel_path, e.sha256, e.size, e.truncated, e.success, e.error "
    "FROM evidence e JOIN assets a ON a.id = e.asset_id "
    "JOIN run_assets ra ON ra.run_id = e.run_id AND ra.asset_id = e.asset_id "
    "LEFT JOIN run_checklist_items rci ON rci.id = e.run_checklist_item_id "
    "LEFT JOIN checklist_items ci ON ci.id = rci.checklist_item_id ")


def _js(v):
    return json.dumps(v, ensure_ascii=False) if v is not None else None


class LegacyStorage:
    def __init__(self, conn, evidence_dir: Path):
        self.conn, self.evidence_dir = conn, Path(evidence_dir)

    def get_run(self, run_id):
        r = self.conn.execute(
            "SELECT r.*, cv.checklist_id, cv.version AS checklist_version, cv.sha256 AS checklist_sha256 "
            "FROM runs r JOIN checklist_versions cv ON cv.id = r.checklist_version_id WHERE r.id = %s",
            (run_id,)).fetchone()
        if r is None:
            return None
        created = json.loads(self.audit_events(run_id)[0]["details"])
        return {"run_id": str(r["id"]), "plan_json": _js(created["plan"]), "plan_sha256": "",
                "checklist_id": r["checklist_id"], "checklist_version": r["checklist_version"],
                "checklist_sha256": r["checklist_sha256"], "engagement_id": r["engagement_id"],
                "status": r["status"], "created_at": r["created_at"].isoformat(timespec="seconds"),
                "updated_at": r["updated_at"].isoformat(timespec="seconds"), "error": r["stop_reason"]}

    def _result(self, r):
        not_run = r["verdict"] == "NOT_RUN"
        return {"run_id": r["run_id"], "check_id": r["check_id"], "target_id": r["target_id"],
                "verdict": _TO_LEGACY.get(r["verdict"], r["verdict"]),
                "method": "manual_review" if r["decided_by"] == "reviewer" else r["body"]["verification_method"]["type"],
                "reason": "미실행(NOT_RUN): 이 항목은 실행되지 않았음" if not_run else r["reason"],
                "rule_outcome": _js(r["rule_outcome"]), "llm_outcome": _js(r["llm_outcome"]),
                "evidence_ids": _js(r["evidence_ids"]), "checklist_id": r["checklist_id"],
                "checklist_version": r["checklist_version"], "rule_version": r["body"]["rule_version"],
                "decided_at": r["updated_at"].isoformat(timespec="seconds")}

    def results(self, run_id):
        return [self._result(r) for r in self.conn.execute(
            _RESULTS + "WHERE rci.run_id = %s ORDER BY ci.check_id, ra.target_id", (run_id,))]

    def get_result(self, run_id, check_id, target_id):
        r = self.conn.execute(_RESULTS + "WHERE rci.run_id=%s AND ci.check_id=%s AND ra.target_id=%s",
                              (run_id, check_id, target_id)).fetchone()
        return self._result(r) if r else None

    def _evidence(self, e):
        args, rel = e.pop("args"), e.pop("rel_path")
        return {**e, "collected_at": e["collected_at"].isoformat(timespec="seconds"), "args_json": _js(args),
                "raw_path": str(self.evidence_dir / rel) if rel else None,
                "truncated": int(e["truncated"]), "success": int(e["success"])}

    def evidence(self, run_id=None, evidence_id=None):
        if evidence_id:
            e = self.conn.execute(_EVIDENCE + "WHERE e.id::text = %s", (evidence_id,)).fetchone()
            return self._evidence(e) if e else None
        return [self._evidence(e) for e in self.conn.execute(
            _EVIDENCE + "WHERE e.run_id = %s ORDER BY e.collected_at, e.id", (run_id,))]

    def history(self, run_id):
        return [{**h, "old_verdict": _TO_LEGACY.get(h["old_verdict"], h["old_verdict"]),
                 "new_verdict": _TO_LEGACY.get(h["new_verdict"], h["new_verdict"]),
                 "changed_at": h["changed_at"].isoformat(timespec="seconds")} for h in self.conn.execute(
            "SELECT vh.id, rci.run_id::text AS run_id, ci.check_id, ra.target_id, vh.old_verdict, vh.new_verdict, "
            "vh.reason, vh.actor, vh.changed_at FROM verdict_history vh "
            "JOIN run_checklist_items rci ON rci.id = vh.run_checklist_item_id "
            "JOIN checklist_items ci ON ci.id = rci.checklist_item_id "
            "JOIN run_assets ra ON ra.run_id = rci.run_id AND ra.asset_id = rci.asset_id "
            "WHERE rci.run_id = %s ORDER BY vh.id", (run_id,))]

    def audit_events(self, run_id):
        return [{"id": e["id"], "run_id": str(e["run_id"]), "ts": e["ts"].isoformat(timespec="seconds"),
                 "event": e["event"], "details": _js(e["details"])} for e in self.conn.execute(
            "SELECT * FROM audit_events WHERE run_id = %s ORDER BY id", (run_id,))]

    def model_calls(self, run_id):
        return [{**c, "ok": int(c["ok"])} for c in self.conn.execute(
            "SELECT * FROM model_calls WHERE run_id = %s ORDER BY id", (run_id,))]

    def audit(self, run_id, event, details):
        db.audit(self.conn, run_id, event, details)
        self.conn.commit()


def coverage(conn, run_id) -> dict:
    """보고서 상태 표시용 사실: 실제 판정 분포, 작업 상태, 차단·실패 내역."""
    q = lambda sql: conn.execute(sql, (run_id,)).fetchall()  # noqa: E731
    return {
        "verdicts": {r["verdict"]: r["n"] for r in q(
            "SELECT verdict, count(*) AS n FROM run_checklist_items WHERE run_id=%s GROUP BY 1 ORDER BY 1")},
        "backlog": {r["status"]: r["n"] for r in q(
            "SELECT status, count(*) AS n FROM backlog_items WHERE run_id=%s GROUP BY 1 ORDER BY 1")},
        "blocked": q("SELECT kind, reason, policy_class, blocked_reason FROM backlog_items WHERE run_id=%s "
                     "AND status='BLOCKED' ORDER BY created_at"),
        "tool_failures": q("SELECT tool, policy_decision, status, error FROM tool_invocations WHERE run_id=%s "
                           "AND status <> 'SUCCEEDED' ORDER BY started_at"),
        "explorations": q("SELECT state, payload->>'tool' AS tool, payload->>'rationale' AS rationale "
                          "FROM exploration_nodes WHERE run_id=%s AND kind='intent' ORDER BY created_at"),
    }


COVERAGE_HEADER = ["구분", "항목", "값", "비고"]
COVERAGE_TITLE = "커버리지 및 제한 사항"


def _coverage_rows(cov: dict) -> list[list]:
    note = "NOT_RUN 은 판정 결과 표에서 INCONCLUSIVE(진단불가)로 표시됨"
    rows = [["판정(실제)", v, n, note if v == "NOT_RUN" else ""] for v, n in cov["verdicts"].items()]
    rows += [["작업 상태", s, n, ""] for s, n in cov["backlog"].items()]
    rows += [["차단된 작업", b["reason"], b["policy_class"] or "", b["blocked_reason"] or ""] for b in cov["blocked"]]
    rows += [["도구 실패/거부", t["tool"], f"{t['policy_decision']}/{t['status']}", t["error"] or ""]
             for t in cov["tool_failures"]]
    rows += [["탐색(가설, 판정 아님)", e["tool"], e["state"], e["rationale"]] for e in cov["explorations"]]
    return rows


def _open_items(cov: dict) -> int:
    return sum(cov["verdicts"].get(v, 0) for v in ("NOT_RUN", "NEEDS_REVIEW", "INCONCLUSIVE"))


def _add_coverage_sheet(xlsx: Path, cov: dict):
    from openpyxl import load_workbook
    wb = load_workbook(str(xlsx))
    ws = wb.create_sheet("커버리지")
    ws.append(COVERAGE_HEADER)
    for row in _coverage_rows(cov):
        ws.append(row)
    wb.save(str(xlsx))


def _add_coverage_section(docx: Path, cov: dict):
    """기본 Word 보고서 끝에 실제 커버리지 표를 덧붙인다(미실행·검토 필요·도구 실패·차단)."""
    from docx import Document
    doc = Document(str(docx))
    doc.add_paragraph().add_run(COVERAGE_TITLE).bold = True
    n = _open_items(cov)
    doc.add_paragraph(f"미확정 항목(미실행/검토 필요/진단불가) {n}건이 남아 있어 진단이 완전히 끝난 것이 아니다." if n
                      else "미확정 항목 없음.")
    rows = _coverage_rows(cov)
    table = doc.add_table(rows=1 + len(rows), cols=len(COVERAGE_HEADER))
    for r, row in enumerate([COVERAGE_HEADER] + rows):
        for c, value in enumerate(row):
            table.cell(r, c).text = _m_text(value)
    doc.save(str(docx))


def _m_text(v) -> str:
    from audit.policy import mask_secrets
    return mask_secrets("" if v is None else str(v))[:500]


_KIND_BY_PLATFORM = {"WEB": "web"}  # audit.pentest_report 가 지원하는 결과보고서 템플릿 유형


def default_kind(conn, run: dict) -> str | None:
    """실행 대상의 platform 으로 결과보고서 템플릿 유형을 고른다. 지원하는 템플릿이 없으면 None."""
    kinds = {_KIND_BY_PLATFORM.get(r["platform"]) for r in conn.execute(
        "SELECT DISTINCT a.platform FROM run_assets ra JOIN assets a ON a.id = ra.asset_id WHERE ra.run_id = %s",
        (run["id"],))}
    return kinds.pop() if len(kinds) == 1 else None


def _free(path: Path) -> Path:
    """기존 보고서를 덮어쓰지 않는다(audit.report._free 와 같은 규칙)."""
    n, p = 1, path
    while p.exists():
        n += 1
        p = path.with_name(f"{path.stem}-{n}{path.suffix}")
    return p


def _m(v) -> str:
    return mask_secrets("" if v is None else str(v))


def _table(doc, header, rows):
    t = doc.add_table(rows=1, cols=len(header))
    try:
        t.style = "Table Grid"
    except (KeyError, ValueError):
        pass
    for cell, h in zip(t.rows[0].cells, header):
        cell.text = h
    for row in rows:
        for cell, v in zip(t.add_row().cells, row):
            cell.text = v if isinstance(v, str) else str(v)
    return t


def _autonomous_data(conn, run: dict) -> dict:
    """체크리스트 없는 실행의 보고 데이터: 탐색 그래프·증적·발견 사항·LLM 호출. PASS/FAIL 판정은 없다 —
    체크리스트 기반 판정이 아니라 탐색 가설/사실/발견이라서다(CLAUDE.md §3.3/§9).
    """
    run_id = run["id"]
    targets = conn.execute(
        "SELECT ra.target_id, a.kind, a.value, a.platform FROM run_assets ra "
        "JOIN assets a ON a.id = ra.asset_id WHERE ra.run_id=%s", (run_id,)).fetchall()
    nodes = conn.execute(
        "SELECT * FROM exploration_nodes WHERE run_id=%s ORDER BY created_at", (run_id,)).fetchall()
    edges = conn.execute(
        "SELECT e.src_id, e.rel, e.dst_id FROM exploration_edges e JOIN exploration_nodes n ON n.id = e.src_id "
        "WHERE n.run_id=%s", (run_id,)).fetchall()
    findings = conn.execute(
        "SELECT f.*, array_agg(fe.evidence_id::text) AS evidence_ids FROM findings f "
        "LEFT JOIN finding_evidence fe ON fe.finding_id = f.id WHERE f.run_id=%s "
        "GROUP BY f.id ORDER BY f.created_at", (run_id,)).fetchall()
    evidence = conn.execute(
        "SELECT * FROM evidence WHERE run_id=%s ORDER BY collected_at", (run_id,)).fetchall()
    calls = conn.execute("SELECT * FROM model_calls WHERE run_id=%s", (run_id,)).fetchall()
    return {"run": run, "targets": targets, "nodes": nodes, "edges": edges, "findings": findings,
            "evidence": evidence,
            "model_calls": {"total": len(calls), "ok": sum(c["ok"] for c in calls),
                            "prompt_tokens": sum(c["prompt_tokens"] or 0 for c in calls),
                            "completion_tokens": sum(c["completion_tokens"] or 0 for c in calls)}}


def _write_autonomous_docx(m: dict, path: Path):
    from docx import Document
    run = m["run"]
    doc = Document()
    doc.add_heading("자율 탐색 보고서 (초안 — 사람 검토 전)", 0)
    doc.add_paragraph("이 보고서는 체크리스트 기반 PASS/FAIL 판정이 아니라 자율 탐색이 만든 가설(intent)·"
                      "사실(fact)·발견 사항(finding)을 모은 초안이다. CONFIRMED 로 표시된 항목도 결정론적 "
                      "신호 일치(RULE_CONFIRMED)일 뿐 사람의 최종 검토를 거치지 않았다(CLAUDE.md §9/§10).")

    doc.add_heading("1. 실행 개요", 1)
    _table(doc, ["항목", "내용"], [
        ["실행 ID", str(run["id"])], ["Engagement", run["engagement_id"]], ["운영자", run["operator"]],
        ["상태", run["status"]], ["자율 모드", run["autonomy"]],
        ["시작", str(run["created_at"])], ["종료", str(run["ended_at"] or "-")]])

    doc.add_heading("2. 대상", 1)
    _table(doc, ["대상ID", "유형", "값", "platform"],
           [[t["target_id"], t["kind"], t["value"], t["platform"] or "-"] for t in m["targets"]])

    doc.add_heading("3. 탐색 그래프 (goal → intent → fact/finding)", 1)
    doc.add_paragraph(f"LLM 호출 {m['model_calls']['total']}회(성공 {m['model_calls']['ok']}), "
                      f"토큰 입력 {m['model_calls']['prompt_tokens']}/출력 {m['model_calls']['completion_tokens']}")
    _table(doc, ["노드ID", "종류", "상태", "출처", "내용"], [
        [str(n["id"])[:8], n["kind"], n["state"], n["origin"],
         _m((n["payload"].get("text") or n["payload"].get("rationale") or n["payload"].get("tool", ""))[:200])]
        for n in m["nodes"]])

    doc.add_heading("4. 발견 사항 (CANDIDATE — 사람 검토 전)", 1)
    if not m["findings"]:
        doc.add_paragraph("탐색 과정에서 결정론적 신호가 확정한 발견 사항이 없음.")
    for f in m["findings"]:
        doc.add_heading(f"{f['title']} — {f['severity']} ({f['verification']})", 2)
        doc.add_paragraph(f"판정 근거: {_m(f['rationale'])}")
        if f["remediation"]:
            doc.add_paragraph(f"조치 권고: {_m(f['remediation'])}")
        doc.add_paragraph(f"증적: {', '.join(e for e in (f['evidence_ids'] or []) if e) or '없음'}")

    doc.add_heading("5. 증적 목록", 1)
    _table(doc, ["증적ID", "도구", "수집시각", "성공", "SHA-256"],
           [[str(e["id"])[:8], e["tool"], str(e["collected_at"]), "Y" if e["success"] else "N",
             e["sha256"] or "-"] for e in m["evidence"]])

    doc.add_heading("6. 제한 사항", 1)
    doc.add_paragraph("체크리스트가 없는 실행이므로 PASS/FAIL 판정, 커버리지 비율은 제공하지 않는다. "
                      "이 실행이 다룬 범위는 §1 '대상' 과 §3 탐색 그래프가 실제로 생성한 작업으로 한정되며, "
                      "다루지 않은 영역에 대한 보장은 없다. 발견 사항은 사람이 확인·승인하기 전까지 최종 결과가 "
                      "아니다(검토 워크플로는 아직 구현되지 않음).")
    doc.save(str(path))


def _write_autonomous_xlsx(m: dict, path: Path):
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)

    ws = wb.create_sheet("개요")
    run = m["run"]
    for row in [["실행ID", str(run["id"])], ["Engagement", run["engagement_id"]], ["운영자", run["operator"]],
                ["상태", run["status"]], ["자율모드", run["autonomy"]]]:
        ws.append(row)

    ws = wb.create_sheet("탐색")
    ws.append(["노드ID", "종류", "상태", "출처", "내용", "생성시각"])
    for n in m["nodes"]:
        ws.append([str(n["id"]), n["kind"], n["state"], n["origin"],
                   _m((n["payload"].get("text") or n["payload"].get("rationale") or n["payload"].get("tool", ""))),
                   str(n["created_at"])])

    ws = wb.create_sheet("발견사항")
    ws.append(["제목", "심각도", "검증", "판정근거", "조치권고", "증적ID"])
    for f in m["findings"]:
        ws.append([f["title"], f["severity"], f["verification"], _m(f["rationale"]), f["remediation"],
                   ", ".join(e for e in (f["evidence_ids"] or []) if e)])

    ws = wb.create_sheet("증적")
    ws.append(["증적ID", "도구", "수집시각", "성공", "SHA-256", "오류"])
    for e in m["evidence"]:
        ws.append([str(e["id"]), e["tool"], str(e["collected_at"]), "Y" if e["success"] else "N",
                   e["sha256"] or "", _m(e["error"])])
    wb.save(str(path))


def generate_autonomous(cfg, conn, run: dict) -> dict:
    """체크리스트 없는(자율 탐색 전용) 실행의 보고서. audit.report 와 달리 PASS/FAIL 판정 표를 만들지
    않는다 — 그런 판정이 아예 없기 때문이다. 탐색 그래프·발견 사항·증적만으로 초안을 만든다.
    """
    if run["status"] != "COMPLETED":
        raise AuditError(f"실행 {run['id']} 의 상태가 {run['status']} 임. COMPLETED 상태의 실행만 보고서로 출력할 수 있음")
    run_id = str(run["id"])
    m = _autonomous_data(conn, run)
    out = cfg.settings.path("reports_dir") / run_id
    out.mkdir(parents=True, exist_ok=True)
    docx_path, xlsx_path = _free(out / f"report_{run_id}.docx"), _free(out / f"report_{run_id}.xlsx")
    _write_autonomous_docx(m, docx_path)
    _write_autonomous_xlsx(m, xlsx_path)
    result = {"run_id": run_id, "docx": str(docx_path), "xlsx": str(xlsx_path),
              "findings": len(m["findings"]), "evidence": len(m["evidence"]), "problems": [],
              "warnings": ["체크리스트 없는 실행 — PASS/FAIL 판정 없음. 발견 사항은 사람 검토 전 초안임"]}
    db.audit(conn, run["id"], "report_generated", result)
    conn.commit()
    return {**result, "coverage": coverage(conn, run["id"])}


def generate(cfg, conn, run: dict, kind=None, meta=None, findings=None) -> dict:
    run_id = str(run["id"])
    if run["checklist_version_id"] is None:
        return generate_autonomous(cfg, conn, run)
    storage = LegacyStorage(conn, cfg.evidence_dir)
    checklists = Checklists(cfg.settings.path("checklist_dir"))
    cov = coverage(conn, run["id"])
    if kind:  # 회사 템플릿 기반 모의해킹 결과보고서(Word)
        from audit import pentest_report
        res = pentest_report.generate(storage, run_id, checklists, cfg.settings, kind, meta, findings)
    else:
        from audit import report
        res = report.generate(storage, run_id, checklists, cfg.settings, EvidenceStore(storage, cfg.evidence_dir))
        _add_coverage_sheet(Path(res["xlsx"]), cov)
        _add_coverage_section(Path(res["docx"]), cov)
        from docx import Document  # 다시 열어 덧붙인 내용이 실제로 들어갔는지 확인한다
        from openpyxl import load_workbook
        sheets = load_workbook(res["xlsx"], read_only=True).sheetnames
        if "커버리지" not in sheets or "판정결과" not in sheets:
            res["problems"].append(f"xlsx: 예상 시트가 없음 {sheets}")
        doc = Document(res["docx"])
        if not any(p.text == COVERAGE_TITLE for p in doc.paragraphs) or                 [c.text for c in doc.tables[-1].rows[0].cells] != COVERAGE_HEADER:
            res["problems"].append("docx: 커버리지 절이 없음")
    return {**res, "coverage": cov}
