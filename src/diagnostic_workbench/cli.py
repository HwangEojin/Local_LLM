"""CLI. 종료 코드: 0 성공, 1 실패/거부/무효, 2 사용법 오류(argparse)."""
from __future__ import annotations

import argparse
import json
import sys

from audit import config as audit_config
from audit.models import AuditError
from audit.policy import mask_secrets

from . import __version__, backlog, config, coordinator, db, evidence, policy, planner, reporting, runs, traces, worker

VERDICTS = ["PASS", "FAIL", "NOT_APPLICABLE", "NEEDS_REVIEW", "INCONCLUSIVE"]


def _open(args, need_schema=True):
    cfg = config.load(args.home)
    conn = db.connect(cfg.dsn)
    if need_schema:
        db.require_migrated(conn, cfg.migrations_dir)
    return cfg, conn


def cmd_doctor(args):
    rows, conn = [], None

    def check(name, fn, warn=False):
        try:
            rows.append(("OK", name, fn()))
        except Exception as e:  # 진단 명령은 가능한 한 끝까지 점검한다
            rows.append(("WARN" if warn else "FAIL", name, f"{type(e).__name__}: {e}"))

    rows.append(("OK" if sys.version_info >= (3, 12) else "FAIL", "파이썬", sys.version.split()[0]))
    try:
        cfg = config.load(args.home)
        rows.append(("OK", "설정", f"home={cfg.home} workers=1 lease={cfg.lease_s}s 예산={cfg.budget}"))
    except AuditError as e:
        rows.append(("FAIL", "설정", str(e)))
        return _print_rows(rows)
    try:
        conn = db.connect(cfg.dsn)
        rows.append(("OK", "PostgreSQL", conn.execute("SELECT version() AS v").fetchone()["v"].split(",")[0]))
        applied, pending = db.migration_status(conn, cfg.migrations_dir)
        rows.append(("FAIL" if pending else "OK", "마이그레이션", f"적용 {len(applied)}개, 미적용 {pending or '없음'}"))
    except AuditError as e:
        rows.append(("FAIL", "PostgreSQL", str(e)))
    check("진단 범위(scope)", lambda: f"대상 {len(audit_config.load_scope(cfg.home)['targets'])}개, "
                                    f"제외 {len(cfg.exclude)}개, 중지 스위치 {'켜짐' if cfg.stop_file.exists() else '꺼짐'}")
    check("증적 디렉터리", lambda: str(cfg.evidence_dir))

    def ollama():
        from audit.local_model import OllamaClient
        info = OllamaClient(cfg.settings.ollama, cfg.settings.external_api_enabled).check_ready()
        return f"v{info['version']} 모델={info['model']}"
    check("로컬 LLM(Ollama)", ollama, warn=True)
    try:
        for name, srv in audit_config.load_mcp_servers(cfg.home).items():
            if not srv["enabled"]:
                rows.append(("INFO", f"MCP {name}", "비활성"))
            elif not args.probe_mcp:
                rows.append(("INFO", f"MCP {name}", "활성, 미확인 (--probe-mcp 로 도구 목록 조회)"))
            else:
                from audit import mcp_client
                # 도구 목록 수신까지만 확인한다. 실제 도구 출력은 검증하지 않았으므로 CONNECTED_UNVERIFIED 다.
                check(f"MCP {name}", lambda s=srv: f"CONNECTED_UNVERIFIED: {(i := mcp_client.list_tools(s))['server']}, "
                                                    f"도구 {len(i['tools'])}개")
    except AuditError as e:
        rows.append(("FAIL", "MCP 설정", str(e)))
    if conn:
        conn.close()
    return _print_rows(rows)


def _print_rows(rows):
    for status, name, detail in rows:
        print(f"[{status:4}] {name}: {detail}")
    return int(any(s == "FAIL" for s, _, _ in rows))


def cmd_migrate(args):
    cfg, conn = _open(args, need_schema=False)
    if args.status:
        applied, pending = db.migration_status(conn, cfg.migrations_dir)
        print(f"적용됨: {applied or '없음'}\n미적용: {pending or '없음'}")
        return 0
    done = db.migrate(conn, cfg.migrations_dir)
    print(f"적용한 마이그레이션: {done or '없음(이미 최신)'}")
    return 0


def _status(conn, run):
    rid = run["id"]
    cv = conn.execute("SELECT checklist_id, version FROM checklist_versions WHERE id=%s",
                      (run["checklist_version_id"],)).fetchone()
    print(f"실행 {rid}\n  상태={run['status']}" + (f" ({run['stop_reason']})" if run["stop_reason"] else "")
          + f"  체크리스트={cv['checklist_id']}@{cv['version']}  운영자={run['operator']}  use_llm={run['use_llm']}")
    cov = reporting.coverage(conn, rid)
    print("  판정: " + (", ".join(f"{k}={v}" for k, v in cov["verdicts"].items()) or "없음"))
    print("  작업: " + (", ".join(f"{k}={v}" for k, v in cov["backlog"].items()) or "없음"))
    n = conn.execute(
        "SELECT (SELECT count(*) FROM tool_invocations WHERE run_id=%(r)s AND status<>'SKIPPED') AS tools, "
        "(SELECT count(*) FROM evidence WHERE run_id=%(r)s AND success) AS ev, "
        "(SELECT count(*) FROM findings WHERE run_id=%(r)s) AS findings, "
        "(SELECT count(*) FROM model_calls WHERE run_id=%(r)s) AS llm", {"r": rid}).fetchone()
    print(f"  도구 호출={n['tools']}/{run['budget']['max_tool_calls']}  증적={n['ev']}  발견 사항={n['findings']}  "
          f"LLM 호출={n['llm']}  탐색={len(cov['explorations'])}/{run['budget']['max_explorations']}")
    for b in cov["blocked"]:
        print(f"  차단: [{b['policy_class'] or '선행대기'}] {b['reason']} — {b['blocked_reason']}")
    open_items = sum(cov["verdicts"].get(v, 0) for v in ("NOT_RUN", "NEEDS_REVIEW", "INCONCLUSIVE"))
    if open_items:
        print(f"  미확정 {open_items}건(미실행/검토 필요/진단불가): 진단이 끝난 것이 아님. 'review' 로 검토 결과를 기록하세요")


def cmd_run(args):
    cfg, conn = _open(args)
    if args.action == "create":
        if not (args.checklist and args.targets and args.operator):
            print("run create 에는 --checklist ID@VERSION --targets a,b --operator 이름 이 필요합니다", file=sys.stderr)
            return 2
        cid, _, ver = args.checklist.partition("@")
        budget = config.validate_budget({k: v for k, v in (("max_tasks", args.max_tasks),
                                                           ("max_explorations", args.max_explorations)) if v is not None})
        run = runs.create_run(conn, cfg, cid, ver, args.targets.split(","), args.operator,
                              args.check_ids.split(",") if args.check_ids else None, args.llm, budget)
        print(f"실행 생성됨: {run['id']}\n미리 보기: run plan {run['id']}   시작: run start {run['id']}")
        return 0
    if args.action == "list":
        for r in conn.execute("SELECT id, status, operator, created_at FROM runs ORDER BY created_at DESC LIMIT 30"):
            print(f"{r['id']}  {r['status']:<10} {r['operator']:<16} {r['created_at']:%Y-%m-%d %H:%M}Z")
        return 0
    if not args.run:
        print(f"run {args.action} 에는 실행 ID(접두어 가능)가 필요합니다", file=sys.stderr)
        return 2
    run = db.resolve_run(conn, args.run)
    if args.action == "plan":  # 실행 예정 작업과 정책 분류 미리 보기(도구는 실행하지 않는다)
        stats = planner.plan(runs.build_context(cfg, conn, run))
        print(f"계획: 새 작업 {stats['created']}건, READY {stats['ready']}건, BLOCKED {stats['blocked']}건")
        return cmd_backlog(args, cfg, conn)
    if args.action == "start":
        final = coordinator.run_loop(cfg, conn, run)
        _status(conn, final)
        return 0 if final["status"] in ("COMPLETED", "PAUSED", "RUNNING") else 1
    if args.action == "cancel":
        print(f"취소됨: 열린 작업 {coordinator.cancel(conn, run, args.operator or 'operator')}건")
        return 0
    _status(conn, run)
    return 0


def cmd_backlog(args, cfg=None, conn=None):
    if conn is None:
        cfg, conn = _open(args)
    run = db.resolve_run(conn, args.run)
    for b in conn.execute("SELECT * FROM backlog_items WHERE run_id=%s AND (%s::text IS NULL OR status=%s) "
                          "ORDER BY priority, created_at", (run["id"], getattr(args, "status", None),
                                                            getattr(args, "status", None))):
        print(f"{str(b['id'])[:8]}  {b['status']:<12} {b['kind']:<11} p{b['priority']:<3} "
              f"{b['policy_class'] or '-':<20} 시도 {b['attempts']}/{b['max_attempts']}  {b['reason'][:70]}"
              + (f"\n          사유: {b['blocked_reason'] or b['last_error']}" if b['blocked_reason'] or b['last_error'] else ""))
    return 0


def _item(conn, run, ref):
    rows = conn.execute("SELECT * FROM backlog_items WHERE run_id=%s AND id::text LIKE %s",
                        (run["id"], ref.replace("%", "") + "%")).fetchall()
    if len(rows) != 1:
        raise AuditError(f"작업 '{ref}' 을(를) 하나로 특정할 수 없음 ({len(rows)}건)")
    return rows[0]


def cmd_approve(args):
    """승인 대기 작업의 도구 호출을 정확한 대상·도구·인자 단위로 승인/거부한다."""
    cfg, conn = _open(args)
    run = db.resolve_run(conn, args.run)
    ctx = runs.build_context(cfg, conn, run)
    item = _item(conn, run, args.item)
    it, n = worker.load_item(ctx, item), 0
    for _, tool, call_args in worker.calls(it):
        d = policy.decide(ctx, tool, call_args, item["asset_id"])
        if d.decision != policy.ALLOW_WITH_APPROVAL:
            continue
        print(f"{'거부' if args.reject else '승인'}: {tool} 인자={mask_secrets(json.dumps(d.args, ensure_ascii=False))}")
        conn.execute("INSERT INTO approvals (run_id, asset_id, tool, args_sha256, approver, decision, expires_at) "
                     "VALUES (%s,%s,%s,%s,%s,%s, now() + make_interval(hours => %s))",
                     (run["id"], item["asset_id"], tool, policy.args_hash(d.args), args.approver,
                      "REJECTED" if args.reject else "APPROVED", cfg.approval_ttl_h))
        db.audit(conn, run["id"], "approval_recorded", {"backlog_item": str(item["id"]), "tool": tool, "args": d.args,
                                                         "approver": args.approver, "rejected": args.reject})
        n += 1
    db.notify_change(conn, run["id"], "approval", item["id"])
    conn.commit()
    print(f"기록된 결정 {n}건 (유효 {cfg.approval_ttl_h}시간). 이어서: run start {run['id']}" if n
          else "이 작업에는 승인 대기 중인 도구 호출이 없음")
    return 0 if n else 1


def cmd_retry(args):
    cfg, conn = _open(args)
    run = db.resolve_run(conn, args.run)
    item = _item(conn, run, args.item)
    if item["status"] not in ("FAILED", "BLOCKED"):
        raise AuditError(f"FAILED/BLOCKED 작업만 다시 시도할 수 있음 (현재 {item['status']})")
    conn.execute("UPDATE backlog_items SET status='PENDING', policy_class=NULL, blocked_reason=NULL, attempts=0, "
                 "updated_at=now() WHERE id=%s", (item["id"],))  # 명시적 재시도: 정책 판정부터 다시 받는다
    db.audit(conn, run["id"], "retry_requested", {"backlog_item": str(item["id"]), "operator": args.operator,
                                                   "reason": args.reason})
    db.notify_change(conn, run["id"], "retry", item["id"])
    if run["status"] == "COMPLETED":
        conn.execute("UPDATE runs SET status='PAUSED', stop_reason='재시도 요청', ended_at=NULL WHERE id=%s", (run["id"],))
    conn.commit()
    print(f"재시도 등록됨. 이어서: run start {run['id']}")
    return 0


def cmd_review(args):
    cfg, conn = _open(args)
    run = db.resolve_run(conn, args.run)
    r = conn.execute(
        "SELECT rci.id, rci.verdict, rci.asset_id, ci.body FROM run_checklist_items rci "
        "JOIN checklist_items ci ON ci.id = rci.checklist_item_id "
        "JOIN run_assets ra ON ra.run_id = rci.run_id AND ra.asset_id = rci.asset_id "
        "WHERE rci.run_id=%s AND ci.check_id=%s AND ra.target_id=%s", (run["id"], args.check_id, args.target_id)).fetchone()
    if r is None:
        raise AuditError("해당 실행에 그 항목/대상이 없음")
    ev = [e["id"] for e in conn.execute("SELECT id FROM evidence WHERE run_checklist_item_id=%s AND success", (r["id"],))]
    if args.verdict in ("PASS", "FAIL") and not ev:
        raise AuditError("PASS/FAIL 에는 연결된 증적이 필요함. 먼저 증적을 수집하거나 NEEDS_REVIEW/INCONCLUSIVE 로 두세요")
    reason = f"수동 검토({args.reviewer}): {args.reason}"
    worker.set_verdict(conn, r["id"], args.verdict, reason, actor=f"reviewer:{args.reviewer}", decided_by="reviewer",
                       evidence_ids=ev)
    if args.verdict == "FAIL":
        conn.execute("INSERT INTO findings (run_id, asset_id, run_checklist_item_id, title, severity, rationale, "
                     "remediation, verification) VALUES (%s,%s,%s,%s,%s,%s,%s,'REVIEWER_CONFIRMED') "
                     "ON CONFLICT (run_id, run_checklist_item_id) DO NOTHING",
                     (run["id"], r["asset_id"], r["id"], r["body"]["title"], r["body"]["severity"], reason,
                      r["body"]["remediation"]))
    db.audit(conn, run["id"], "verdict_changed", {"check_id": args.check_id, "target_id": args.target_id,
                                                   "old": r["verdict"], "new": args.verdict, "reviewer": args.reviewer})
    conn.commit()
    print(f"{args.check_id}/{args.target_id}: {r['verdict']} -> {args.verdict} (이력 기록됨)")
    return 0


def cmd_evidence(args):
    cfg, conn = _open(args)
    run = db.resolve_run(conn, args.run)
    bad = 0
    for e in conn.execute("SELECT * FROM evidence WHERE run_id=%s ORDER BY collected_at", (run["id"],)):
        problem = evidence.verify(e, cfg.evidence_dir)
        bad += bool(problem)
        state = problem or ("무결성 확인" if e["success"] else f"수집 실패: {e['error']}")
        print(f"{e['id']}  {e['tool']:<28} {e['requirement_id']:<14} {(e['sha256'] or '-')[:12]}  "
              f"{e['size'] if e['size'] is not None else '-':>7}B  {state}")
    return int(bad > 0)


def cmd_trace(args):
    cfg, conn = _open(args)
    if args.action == "show":
        run = db.resolve_run(conn, args.run)
        rows = traces.get_worker_trace(conn, _item(conn, run, args.query)["id"])
    else:
        exclude = db.resolve_run(conn, args.run)["id"] if args.run else None
        rows = traces.search_all_worker_traces(conn, args.query or "", tool=args.tool, status=args.status,
                                               exclude_run=exclude, limit=args.limit)
    for t in rows:
        print(f"{t['started_at']:%m-%d %H:%M:%S}  run={str(t['run_id'])[:8]} 작업={str(t['backlog_item_id'])[:8]} "
              f"#{t['step_no']} {t['tool']:<26} {t['policy_decision']:<20} {t['status']:<9} "
              f"{t['asset_kind']}:{t['asset_value']}\n    인자={json.dumps(t['args'], ensure_ascii=False)[:200]}"
              + (f"\n    오류={t['error'][:200]}" if t["error"] else "")
              + (f"\n    증적={t['evidence_id']}" if t["evidence_id"] else ""))
    print(f"{len(rows)}건 (과거 과정은 참고용이며 현재 대상의 증적을 대신하지 않음)")
    return 0


def cmd_graph(args):
    cfg, conn = _open(args)
    run = db.resolve_run(conn, args.run)
    print("자산 그래프(이 실행의 대상):")
    for a in conn.execute("SELECT ra.target_id, a.* FROM run_assets ra JOIN assets a ON a.id = ra.asset_id "
                          "WHERE ra.run_id=%s", (run["id"],)):
        print(f"  {str(a['id'])[:8]}  {a['target_id']:<16} {a['kind']}:{a['value']}  [{a['verification']}, 출처={a['source']}]")
    print("탐색 그래프:")
    for n in conn.execute("SELECT * FROM exploration_nodes WHERE run_id=%s ORDER BY created_at", (run["id"],)):
        text = n["payload"].get("text") or n["payload"].get("rationale") or n["payload"].get("tool", "")
        anchors = conn.execute("SELECT count(asset_id) AS a, count(evidence_id) AS e FROM exploration_anchors "
                               "WHERE node_id=%s", (n["id"],)).fetchone()
        print(f"  {str(n['id'])[:8]}  {n['kind']:<7} {n['state']:<9} 출처={n['origin']:<8} "
              f"anchor(자산 {anchors['a']}, 증적 {anchors['e']})  {text[:80]}")
    for e in conn.execute("SELECT e.* FROM exploration_edges e JOIN exploration_nodes n ON n.id = e.src_id "
                          "WHERE n.run_id=%s", (run["id"],)):
        print(f"  {str(e['src_id'])[:8]} -{e['rel']}-> {str(e['dst_id'])[:8]}")
    return 0


def cmd_report(args):
    cfg, conn = _open(args)
    run = db.resolve_run(conn, args.run)
    if args.type and not args.meta:
        print("--type 사용 시 --meta <report_meta.json> 이 필요합니다", file=sys.stderr)
        return 2
    res = reporting.generate(cfg, conn, run, args.type, args.meta, args.findings)
    print(f"docx: {res['docx']}" + (f"\nxlsx: {res['xlsx']}" if "xlsx" in res else ""))
    cov = res["coverage"]
    print("커버리지(실제 판정): " + ", ".join(f"{k}={v}" for k, v in cov["verdicts"].items()))
    open_items = sum(cov["verdicts"].get(v, 0) for v in ("NOT_RUN", "NEEDS_REVIEW", "INCONCLUSIVE"))
    if open_items:
        print(f"주의: 미확정 {open_items}건이 남아 있어 진단이 완전히 끝난 보고서가 아님")
    for w in res.get("warnings", []):
        print(f"  경고: {w}")
    for p in res["problems"]:
        print(f"  검증 실패: {p}")
    return int(bool(res["problems"]))


def build_parser():
    p = argparse.ArgumentParser(prog="diagnostic_workbench", description="로컬 LLM 기반 보안 진단 워크벤치")
    p.add_argument("--home", help="프로젝트 디렉터리 (기본값: $AUDIT_HOME 또는 현재 디렉터리)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor", help="설정, PostgreSQL, 마이그레이션, 범위, LLM, MCP 상태 점검")
    d.add_argument("--probe-mcp", action="store_true", help="활성 MCP 서버에 연결해 도구 목록을 받아 본다")
    m = sub.add_parser("migrate", help="SQL 마이그레이션 적용")
    m.add_argument("--status", action="store_true")
    r = sub.add_parser("run", help="실행 생성/미리 보기/시작(재개)/상태/취소/목록")
    r.add_argument("action", choices=["create", "plan", "start", "status", "cancel", "list"])
    r.add_argument("run", nargs="?")
    r.add_argument("--checklist", help="ID@VERSION (활성화된 체크리스트)")
    r.add_argument("--targets", help="scope.json 의 target_id 목록(쉼표 구분)")
    r.add_argument("--check-ids", help="일부 항목만 실행(쉼표 구분)")
    r.add_argument("--operator")
    r.add_argument("--llm", action="store_true", help="로컬 LLM 참고 의견과 탐색 제안 사용")
    r.add_argument("--max-tasks", type=int)
    r.add_argument("--max-explorations", type=int)
    b = sub.add_parser("backlog", help="작업 큐(TodoList) 조회")
    b.add_argument("run")
    b.add_argument("--status")
    a = sub.add_parser("approve", help="승인 대기 작업의 도구 호출 승인/거부")
    a.add_argument("run")
    a.add_argument("item")
    a.add_argument("--approver", required=True)
    a.add_argument("--reject", action="store_true")
    rt = sub.add_parser("retry", help="FAILED/BLOCKED 작업을 명시적으로 다시 시도")
    rt.add_argument("run")
    rt.add_argument("item")
    rt.add_argument("--operator", required=True)
    rt.add_argument("--reason", required=True)
    rv = sub.add_parser("review", help="사람이 검토한 판정 기록(이력 보존)")
    rv.add_argument("run")
    rv.add_argument("check_id")
    rv.add_argument("target_id")
    rv.add_argument("verdict", choices=VERDICTS)
    rv.add_argument("--reason", required=True)
    rv.add_argument("--reviewer", required=True)
    ev = sub.add_parser("evidence", help="증적 목록과 파일 해시 대조")
    ev.add_argument("run")
    t = sub.add_parser("trace", help="Worker Activity 검색(search) / 작업 단계 상세(show)")
    t.add_argument("action", choices=["search", "show"])
    t.add_argument("query", nargs="?", help="search: 검색어, show: 작업 ID")
    t.add_argument("--run", help="search: 제외할 현재 실행, show: 작업이 속한 실행")
    t.add_argument("--tool")
    t.add_argument("--status", choices=["SKIPPED", "SUCCEEDED", "FAILED"])
    t.add_argument("--limit", type=int, default=20)
    g = sub.add_parser("graph", help="자산 그래프와 탐색 그래프 요약")
    g.add_argument("run")
    rp = sub.add_parser("report", help="Word/Excel 보고서 생성(COMPLETED 실행)")
    rp.add_argument("run")
    rp.add_argument("--type", choices=["web"], help="모의해킹 결과보고서 템플릿 유형")
    rp.add_argument("--meta")
    rp.add_argument("--findings")
    return p


COMMANDS = {"doctor": cmd_doctor, "migrate": cmd_migrate, "run": cmd_run, "backlog": cmd_backlog,
            "approve": cmd_approve, "retry": cmd_retry, "review": cmd_review, "evidence": cmd_evidence,
            "trace": cmd_trace, "graph": cmd_graph, "report": cmd_report}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](args)
    except KeyboardInterrupt:
        print("중단됨. 이어서 실행: run start <run_id>", file=sys.stderr)
        return 1
    except AuditError as e:
        print(f"오류: {type(e).__name__}: {mask_secrets(str(e))}", file=sys.stderr)
        return 1
