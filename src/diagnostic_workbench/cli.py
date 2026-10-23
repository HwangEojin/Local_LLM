"""CLI. 종료 코드: 0 성공, 1 실패/거부/무효, 2 사용법 오류(argparse)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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


def cmd_web(args):
    """로컬 웹 UI 백엔드(FastAPI)를 띄운다. 선택적 의존성(pip install -e .[web])이 필요함.

    새 진단 로직은 없다 — src/webapi 가 이 모듈들을 그대로 부르는 HTTP 어댑터일 뿐이다.
    """
    try:
        import uvicorn
    except ImportError:
        print("웹 UI 의존성이 없음: pip install -e '.[web]' 로 설치하세요", file=sys.stderr)
        return 2
    import os
    config.load(args.home)  # .env 를 지금 읽어 둔다 — 아래 환경 변수 검사가 .env 값을 보게 하려면 필요함
    if not os.environ.get("WORKBENCH_WEB_PASSWORD") or not os.environ.get("WORKBENCH_WEB_SECRET"):
        print("환경 변수 WORKBENCH_WEB_PASSWORD, WORKBENCH_WEB_SECRET 이 .env 에 필요합니다", file=sys.stderr)
        return 2
    uvicorn.run("webapi.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


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


def cmd_checklist(args):
    """체크리스트 목록/검증/활성화, 원본 xlsx 가져오기(파일 기반, 실행 생성 시 DB 로 들어간다)."""
    from audit.checklist_engine import Checklists
    cfg, conn = _open(args)
    cl = Checklists(cfg.settings.path("checklist_dir"))
    if args.action == "list":
        for cid, v in cl.entries():
            print(f"{cid}	{v['version']}	{v['status']}	{v.get('sha256', '-')[:12]}")
        return 0
    if args.action == "validate":
        bad = 0
        for cid, v in cl.entries():
            errs = cl.check_version(cid, v["version"])
            print(f"{cid}@{v['version']} [{v['status']}]: {'정상' if not errs else '무효'}")
            for e in errs:
                print(f"    - {e}")
            bad |= bool(errs)
        return int(bad)
    if args.action == "activate":
        if not (args.name and args.version):
            print("activate 에는 <checklist_id> <version> 이 필요합니다", file=sys.stderr)
            return 2
        sha = cl.activate(args.name, args.version)
        db.audit(conn, None, "checklist_activated", {"checklist_id": args.name, "version": args.version, "sha256": sha})
        conn.commit()
        print(f"활성화됨: {args.name}@{args.version} sha256={sha}")
        return 0
    if args.action == "overlays":
        from audit import overlays as overlays_mod
        path = cfg.home / "config" / "rule_overlays.json"
        try:
            ov = overlays_mod.load_overlays(path)
        except AuditError as e:
            print(f"무효: {e}", file=sys.stderr)
            return 1
        if not ov:
            print(f"오버레이 없음 ({path})")
            return 0
        known = {(cid, it["check_id"]) for cid, v in cl.entries() if v["status"] == "active"
                for it in cl.load(cid, v["version"])[0]["items"]}
        bad = 0
        for (cid, check_id), entry in sorted(ov.items()):
            stale = (cid, check_id) not in known
            bad |= stale
            print(f"{cid}  {check_id}  platforms={entry['platforms']}  "
                  f"{'[활성 체크리스트에 없음]' if stale else 'OK'}")
            print(f"    근거: {entry['basis']}")
        return int(bad)
    if args.action == "reapply-overlays":
        from audit import overlays as overlays_mod
        if not (args.name and args.version):
            print("reapply-overlays 에는 <checklist_id> <version> 이 필요합니다", file=sys.stderr)
            return 2
        ov = overlays_mod.load_overlays(cfg.home / "config" / "rule_overlays.json")
        changed = runs.reapply_overlays(conn, cl, args.name, args.version, ov)
        conn.commit()
        print(f"재계산된 check_id: {changed or '없음(이미 최신)'}")
        return 0
    from audit import importer  # import: 원본 xlsx -> 버전 고정 JSON(draft)
    xlsx, pdf = Path(args.name or ""), Path(args.pdf) if args.pdf else None
    if not xlsx.is_file() or (pdf is not None and not pdf.is_file()):
        print("import 에는 원본 xlsx 경로가 필요합니다: checklist import <xlsx> [version] [--pdf <pdf>]", file=sys.stderr)
        return 2
    version = args.version or "2026-1"
    checklists, notes = importer.convert(xlsx, version=version, pdf=pdf)
    written = importer.write_checklists(checklists, cfg.settings.path("checklist_dir"), version,
                                        [xlsx] + ([pdf] if pdf else []), replace_draft=args.replace_draft)
    db.audit(conn, None, "checklist_imported", {"source": xlsx.name, "sha256": importer.sha256_file(xlsx),
                                                "checklists": list(checklists)})
    conn.commit()
    total = sum(len(c["items"]) for c in checklists.values())
    incomplete = sum(1 for c in checklists.values() for i in c["items"] if i["item_status"] != "complete")
    print(f"변환 완료: 체크리스트 {len(written)}개, 항목 {total}개 (기준 미확보 incomplete {incomplete}개)")
    print("상태는 모두 draft 입니다. 검증 후 활성화하세요: checklist validate / checklist activate <id> <version>")
    return 0


def cmd_tools(args):
    """실행 가능한 도구와 위험도. read 는 바로, 그 밖은 호출마다 승인이 필요하다."""
    cfg = config.load(args.home)
    for name, spec in sorted(runs.registry(cfg).items()):
        print(f"{name:<44} {spec.risk:<10} 범위검사 인자={spec.scope_args or '-'}")
    return 0


def _status(conn, run):
    rid = run["id"]
    if run["checklist_version_id"] is None:
        checklist_desc = "없음(자율 탐색 전용)"
    else:
        cv = conn.execute("SELECT checklist_id, version FROM checklist_versions WHERE id=%s",
                          (run["checklist_version_id"],)).fetchone()
        checklist_desc = f"{cv['checklist_id']}@{cv['version']}"
    print(f"실행 {rid}\n  상태={run['status']}" + (f" ({run['stop_reason']})" if run["stop_reason"] else "")
          + f"  체크리스트={checklist_desc}  운영자={run['operator']}  use_llm={run['use_llm']}"
          + f"  자율모드={run['autonomy']}")
    cov = reporting.coverage(conn, rid)
    print("  판정: " + (", ".join(f"{k}={v}" for k, v in cov["verdicts"].items()) or "없음"))
    print("  작업: " + (", ".join(f"{k}={v}" for k, v in cov["backlog"].items()) or "없음"))
    n = conn.execute(
        "SELECT (SELECT count(*) FROM tool_invocations ti JOIN backlog_items b ON b.id=ti.backlog_item_id "
        " WHERE ti.run_id=%(r)s AND ti.status<>'SKIPPED' AND b.kind='checklist') AS tools, "
        "(SELECT count(*) FROM tool_invocations ti JOIN backlog_items b ON b.id=ti.backlog_item_id "
        " WHERE ti.run_id=%(r)s AND ti.status<>'SKIPPED' AND b.kind='exploration') AS explore_tools, "
        "(SELECT count(*) FROM evidence WHERE run_id=%(r)s AND success) AS ev, "
        "(SELECT count(*) FROM findings WHERE run_id=%(r)s) AS findings, "
        "(SELECT count(*) FROM model_calls WHERE run_id=%(r)s) AS llm", {"r": rid}).fetchone()
    print(f"  도구 호출={n['tools']}/{run['budget']['max_tool_calls']}"
          f"(탐색 별도 {n['explore_tools']}/{run['budget'].get('max_tool_calls_exploration', '-')})  "
          f"증적={n['ev']}  발견 사항={n['findings']}  "
          f"LLM 호출={n['llm']}  탐색={len(cov['explorations'])}/{run['budget']['max_explorations']}")
    for b in cov["blocked"]:
        print(f"  차단: [{b['policy_class'] or '선행대기'}] {b['reason']} — {b['blocked_reason']}")
    open_items = sum(cov["verdicts"].get(v, 0) for v in ("NOT_RUN", "NEEDS_REVIEW", "INCONCLUSIVE"))
    if open_items:
        print(f"  미확정 {open_items}건(미실행/검토 필요/진단불가): 진단이 끝난 것이 아님. 'review' 로 검토 결과를 기록하세요")


def cmd_run(args):
    cfg, conn = _open(args)
    if args.action == "create":
        if not (args.targets and args.operator):
            print("run create 에는 --targets a,b --operator 이름 이 필요합니다 "
                  "(체크리스트 진단은 --checklist ID@VERSION 도 필요, 자율 탐색만 하려면 --llm 필요)", file=sys.stderr)
            return 2
        cid, ver = (args.checklist.partition("@")[0], args.checklist.partition("@")[2]) if args.checklist else (None, None)
        budget = config.validate_budget({k: v for k, v in (("max_tasks", args.max_tasks),
                                                           ("max_explorations", args.max_explorations)) if v is not None})
        run = runs.create_run(conn, cfg, args.targets.split(","), args.operator, checklist_id=cid, version=ver,
                              check_ids=args.check_ids.split(",") if args.check_ids else None, use_llm=args.llm,
                              budget=budget, allowed_tools=args.tools.split(",") if args.tools else None,
                              autonomy=args.autonomy, goal_text=args.goal, llm_provider=args.llm_provider)
        print(f"실행 생성됨: {run['id']}  자율 모드={run['autonomy']}"
              + ("" if cid else "  (체크리스트 없음 — 자율 탐색 전용)")
              + (f"  LLM={run['llm_provider']}" if run["llm_provider"] != "ollama" else ""))
        if run["autonomy"] == "full":
            print("  주의: 위험도 'write/dangerous' 도구가 범위 내에서 사람 승인 없이 자동 실행됩니다. "
                  "실행 중 바꿀 수 없습니다(scope 와 같은 방식으로 고정).")
        print(f"미리 보기: run plan {run['id']}   시작: run start {run['id']}")
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


def _finding(conn, run, ref):
    rows = conn.execute("SELECT * FROM findings WHERE run_id=%s AND id::text LIKE %s",
                        (run["id"], ref.replace("%", "") + "%")).fetchall()
    if len(rows) != 1:
        raise AuditError(f"finding '{ref}' 을(를) 하나로 특정할 수 없음 ({len(rows)}건)")
    return rows[0]


def cmd_finding(args):
    """체크리스트 유무와 무관하게 findings 테이블의 발견 사항을 사람이 검토한다(CLAUDE.md §9/§10).

    체크리스트 FAIL 판정은 'review' 명령으로 이미 검토할 수 있지만, 자율 탐색이 올린 발견 사항
    (run_checklist_item_id 없음)은 그 명령으로 닿지 않아 'finding review' 를 따로 둔다. 둘 다 같은
    findings 테이블, 같은 상태 체계(worker.FINDING_STATUSES)를 쓴다.
    """
    cfg, conn = _open(args)
    run = db.resolve_run(conn, args.run)
    if args.action == "list":
        rows = conn.execute(
            "SELECT * FROM findings WHERE run_id=%s ORDER BY created_at", (run["id"],)).fetchall()
        for f in rows:
            reviewed = f"검토: {f['status']}({f['reviewed_by']})" if f["reviewed_by"] else "검토 전"
            print(f"{str(f['id'])[:8]}  [{f['severity']:<6}] {f['title'][:50]:<50} "
                  f"verification={f['verification']:<17} status={f['status']:<10} {reviewed}")
        print(f"{len(rows)}건")
        return 0
    if args.action == "retest":
        if not (args.finding_id and args.reason and args.reviewer):
            print("finding retest 에는 <finding_id> --reason .. --reviewer .. 가 필요합니다", file=sys.stderr)
            return 2
        f = _finding(conn, run, args.finding_id)
        item_ids = worker.create_retest(conn, run["id"], f["id"], operator=args.reviewer, reason=args.reason)
        db.audit(conn, run["id"], "finding_retest_requested",
                 {"finding_id": str(f["id"]), "reviewer": args.reviewer, "backlog_items": [str(i) for i in item_ids]})
        db.notify_change(conn, run["id"], "fact")  # 다음 계획 주기가 새 탐색 작업을 집어가게 깨운다
        conn.commit()
        print(f"{f['id']}: 재검증 작업 {len(item_ids)}건 생성됨 — run start {run['id']} 로 실행")
        return 0
    if not (args.finding_id and args.status and args.reason and args.reviewer):
        print("finding review 에는 <finding_id> <status> --reason .. --reviewer .. 가 필요합니다", file=sys.stderr)
        return 2
    f = _finding(conn, run, args.finding_id)
    evidence_ids = [e.strip() for e in (args.evidence or "").split(",") if e.strip()]
    old_status = f["status"]
    worker.set_finding_status(conn, f["id"], args.status, args.reason,
                              actor=f"reviewer:{args.reviewer}", evidence_ids=evidence_ids)
    db.audit(conn, run["id"], "finding_status_changed",
             {"finding_id": str(f["id"]), "old": old_status, "new": args.status, "reviewer": args.reviewer})
    conn.commit()
    print(f"{f['id']}: {old_status} -> {args.status} (이력 기록됨)")
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


def cmd_collect(args):
    """운영자가 고른 등록 도구를 실행해 체크리스트 항목에 증적으로 붙인다(주로 수동확인 항목).

    자동 수집과 같은 경로(정책 판정 -> 실행 -> 증적 -> Activity)를 지나며 판정은 바꾸지 않는다.
    --steps 는 브라우저처럼 앞 호출의 상태가 필요한 도구를 한 프로세스에서 차례로 부른다. 단계마다 따로 판정한다.
    """
    cfg, conn = _open(args)
    run = db.resolve_run(conn, args.run)
    if run["status"] == "CANCELLED":
        raise AuditError("취소된 실행에는 증적을 추가할 수 없음")

    def load(raw):
        return json.loads(Path(raw[1:]).read_text(encoding="utf-8") if raw.startswith("@") else raw)
    try:
        steps = load(args.steps) if args.steps else [{"tool": args.tool, "args": load(args.args or "{}")}]
        if not (isinstance(steps, list) and steps and all(isinstance(x, dict) and x.get("tool") for x in steps)):
            raise ValueError('도구가 없음: <도구> 를 주거나 --steps 에 [{"tool": ..., "args": {...}}] 목록을 주세요')
    except (OSError, ValueError) as e:
        print(f"인자를 읽을 수 없습니다(JSON 또는 @파일): {e}", file=sys.stderr)
        return 2
    item = conn.execute(
        "SELECT b.* FROM backlog_items b JOIN run_checklist_items rci ON rci.id = b.run_checklist_item_id "
        "JOIN checklist_items ci ON ci.id = rci.checklist_item_id "
        "JOIN run_assets ra ON ra.run_id = rci.run_id AND ra.asset_id = rci.asset_id "
        "WHERE b.run_id=%s AND ci.check_id=%s AND ra.target_id=%s", (run["id"], args.check_id, args.target_id)).fetchone()
    if item is None:
        raise AuditError("그 항목/대상의 작업이 없음. 먼저 'run plan' 또는 'run start' 를 실행하세요")
    ctx = runs.build_context(cfg, conn, run)
    it = worker.load_item(ctx, item)
    step_no = conn.execute("SELECT COALESCE(max(step_no), 0) AS n FROM tool_invocations WHERE backlog_item_id=%s",
                           (item["id"],)).fetchone()["n"]
    failed = 0
    for st in steps:
        tool, tool_args, step_no = st["tool"], st.get("args", {}), step_no + 1
        if args.approve_as:  # 이 호출의 정확한 대상·도구·인자에 대해서만 승인을 기록한다
            d = policy.decide(ctx, tool, tool_args, item["asset_id"])
            if d.decision == policy.ALLOW_WITH_APPROVAL:
                conn.execute("INSERT INTO approvals (run_id, asset_id, tool, args_sha256, approver, decision, expires_at) "
                             "VALUES (%s,%s,%s,%s,%s,'APPROVED', now() + make_interval(hours => %s))",
                             (run["id"], item["asset_id"], tool, policy.args_hash(d.args), args.approve_as,
                              cfg.approval_ttl_h))
                db.audit(conn, run["id"], "approval_recorded", {"backlog_item": str(item["id"]), "tool": tool,
                                                                 "args": d.args, "approver": args.approve_as})
        eid, out = worker.run_tool(ctx, it, None, step_no, f"manual:{tool}", tool, tool_args)
        row = conn.execute("SELECT * FROM evidence WHERE id=%s", (eid,)).fetchone()
        db.audit(conn, run["id"], "evidence_added", {"check_id": args.check_id, "target_id": args.target_id,
                                                      "tool": tool, "evidence_id": str(eid), "success": row["success"],
                                                      "operator": args.operator})
        if row["success"]:  # 운영자가 손으로 모은 증적도 Planner 를 깨운다(탐색이 이를 근거로 삼을 수 있게)
            db.notify_change(conn, run["id"], "evidence", eid)
        conn.commit()
        if out is None:
            failed += 1
            print(f"수집 실패(기록됨) {tool} {eid}: {row['error']}")
            continue
        print(f"증적 추가됨: {tool} {eid}  {row['size']}B sha256={row['sha256'][:16]}… 도구버전={row['tool_version']}"
              + ("  (잘림)" if row["truncated"] else ""))
        sf = out.meta.get("scope_filter")
        if sf:
            print(f"  범위 필터: 수신 {sf['received']}건 → 저장 {sf['kept']}건 (범위 밖 {sf['dropped_out_of_scope']}건, "
                  f"호스트 불명 {sf['dropped_unknown_host']}건 제외)")
    return int(failed > 0)


def cmd_trace(args):
    cfg, conn = _open(args)
    if args.action == "tag":
        if not (args.run and args.query and args.check_id):
            print("trace tag 에는 <node_id> --run <run> --check-id <check_id> 가 필요합니다", file=sys.stderr)
            return 2
        run = db.resolve_run(conn, args.run)
        tagged = traces.tag_node(conn, run, args.query, args.check_id)
        conn.commit()
        print(f"매핑됨: 탐색 노드 {tagged['node_id']} -> check_id {args.check_id}")
        return 0
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
    for e in conn.execute("SELECT s.value AS src, e.rel, d.value AS dst, e.source FROM asset_edges e "
                          "JOIN assets s ON s.id = e.src_id JOIN assets d ON d.id = e.dst_id WHERE e.run_id=%s "
                          "ORDER BY d.value LIMIT 50", (run["id"],)):
        print(f"  {e['src']} -{e['rel']}-> {e['dst']}  [{e['source'][:21]}]")
    print("탐색 그래프:")
    for n in conn.execute("SELECT * FROM exploration_nodes WHERE run_id=%s ORDER BY created_at", (run["id"],)):
        text = n["payload"].get("text") or n["payload"].get("rationale") or n["payload"].get("tool", "")
        anchors = conn.execute("SELECT count(asset_id) AS a, count(evidence_id) AS e, count(finding_id) AS f, "
                               "count(checklist_item_id) AS c FROM exploration_anchors WHERE node_id=%s",
                               (n["id"],)).fetchone()
        print(f"  {str(n['id'])[:8]}  {n['kind']:<7} {n['state']:<9} 출처={n['origin']:<8} "
              f"anchor(자산 {anchors['a']}, 증적 {anchors['e']}, 발견 {anchors['f']}, check_id {anchors['c']})  "
              f"{text[:80]}")
    for e in conn.execute("SELECT e.* FROM exploration_edges e JOIN exploration_nodes n ON n.id = e.src_id "
                          "WHERE n.run_id=%s", (run["id"],)):
        print(f"  {str(e['src_id'])[:8]} -{e['rel']}-> {str(e['dst_id'])[:8]}")
    return 0


def cmd_report(args):
    cfg, conn = _open(args)
    run = db.resolve_run(conn, args.run)
    no_checklist = run["checklist_version_id"] is None
    if no_checklist and args.basic:  # --basic(audit.report)은 체크리스트 전제라 자율 탐색 실행에는 못 씀
        print("체크리스트 없는 실행은 --basic 을 지원하지 않습니다. --meta 로 결과보고서(초안)를 만드세요",
              file=sys.stderr)
        return 2
    if no_checklist:  # 체크리스트가 없는 실행: pentest_report 서식 그대로, findings 테이블에서 발견 사항을 채움
        if not args.meta:
            print("체크리스트 없는 실행도 결과보고서에는 --meta <report_meta.json> 이 필요합니다(고객사·프로젝트·대상)",
                  file=sys.stderr)
            return 2
        res = reporting.generate(cfg, conn, run, meta=args.meta, findings=args.findings)
        print(f"docx: {res['docx']}")
        for w in res.get("warnings", []):
            print(f"  경고: {w}")
        for p in res["problems"]:
            print(f"  검증 실패: {p}")
        return int(bool(res["problems"]))
    # 기본은 회사 템플릿 서식의 결과보고서다. 내부 점검 요약(표 양식 Word + Excel)은 --basic 으로 만든다.
    kind = None if args.basic else (args.type or reporting.default_kind(conn, run))
    if kind is None and not args.basic:
        print("이 실행의 대상 platform 에 맞는 결과보고서 템플릿이 없습니다(지원: WEB). "
              "--type 으로 지정하거나 --basic 으로 내부 점검 요약을 만드세요", file=sys.stderr)
        return 2
    if kind and not args.meta:
        print(f"결과보고서({kind})에는 --meta <report_meta.json> 이 필요합니다(고객사·프로젝트·대상·일정·인력). "
              "내부 점검 요약만 필요하면 --basic", file=sys.stderr)
        return 2
    res = reporting.generate(cfg, conn, run, kind, args.meta, args.findings)
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
    w = sub.add_parser("web", help="로컬 웹 UI 백엔드(FastAPI) 실행 — pip install -e '.[web]' 필요")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8420)
    w.add_argument("--reload", action="store_true", help="개발용: 코드 변경 시 자동 재시작")
    m = sub.add_parser("migrate", help="SQL 마이그레이션 적용")
    m.add_argument("--status", action="store_true")
    c = sub.add_parser("checklist", help="체크리스트 목록/검증/활성화/원본 xlsx 가져오기")
    c.add_argument("action", choices=["list", "validate", "activate", "import", "overlays", "reapply-overlays"])
    c.add_argument("name", nargs="?", help="activate/reapply-overlays: checklist_id, import: 원본 xlsx 경로")
    c.add_argument("version", nargs="?")
    c.add_argument("--pdf", help="import 시 쪽 번호를 연결할 참고 PDF 경로(pdftotext 필요)")
    c.add_argument("--replace-draft", action="store_true", help="import 시 같은 버전이 draft 이면 교체")
    sub.add_parser("tools", help="등록된 도구와 위험도 목록")
    r = sub.add_parser("run", help="실행 생성/미리 보기/시작(재개)/상태/취소/목록")
    r.add_argument("action", choices=["create", "plan", "start", "status", "cancel", "list"])
    r.add_argument("run", nargs="?")
    r.add_argument("--checklist", help="ID@VERSION (활성화된 체크리스트). 생략하면 체크리스트 없이 자율 탐색만 함(--llm 필요)")
    r.add_argument("--targets", help="scope.json 의 target_id 목록(쉼표 구분)")
    r.add_argument("--check-ids", help="일부 항목만 실행(쉼표 구분, --checklist 있을 때만)")
    r.add_argument("--goal", help="자율 탐색 목표 문구(생략하면 기본 문구 사용, --checklist 없을 때만 의미 있음)")
    r.add_argument("--operator")
    r.add_argument("--tools", help="이 실행에서 허용할 도구 이름(쉼표 구분). 생략하면 등록된 도구 전체")
    r.add_argument("--autonomy", choices=runs.AUTONOMY_LEVELS, default="approval",
                   help="approval(기본, 항상 승인) | full(모두 자동, 범위 내). "
                        "진단 시작 전에만 정하며 실행 중 바꿀 수 없음")
    r.add_argument("--llm", action="store_true", help="로컬 LLM 참고 의견과 탐색 제안 사용")
    r.add_argument("--llm-provider", choices=runs.LLM_PROVIDERS, default="ollama",
                   help="ollama(기본, 로컬) | claude_cli(임시 테스트 — 이 PC 에 로그인된 claude CLI 구독 호출, "
                        "API 과금 아님). --llm 과 함께 쓸 때만 의미 있음")
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
    fd = sub.add_parser("finding", help="findings 목록(list) / 확정·기각 검토(review) / 추가 검증 요청(retest)")
    fd.add_argument("action", choices=["list", "review", "retest"])
    fd.add_argument("run")
    fd.add_argument("finding_id", nargs="?", help="review/retest: 대상 finding id(앞부분만으로도 특정 가능)")
    fd.add_argument("status", nargs="?", choices=worker.FINDING_STATUSES, help="review: 새 상태")
    fd.add_argument("--reason", help="review/retest 필수")
    fd.add_argument("--reviewer", help="review/retest 필수")
    fd.add_argument("--evidence", help="review: 추가로 연결할 증적 id(쉼표로 구분), CONFIRMED 에 필요할 수 있음")
    ev = sub.add_parser("evidence", help="증적 목록과 파일 해시 대조")
    ev.add_argument("run")
    c = sub.add_parser("collect", help="등록된 도구를 한 번 실행해 항목에 증적 추가(판정은 바꾸지 않음)")
    c.add_argument("run")
    c.add_argument("check_id")
    c.add_argument("target_id")
    c.add_argument("tool", nargs="?", help="등록된 도구 이름 (예: mcp:burp/get_proxy_http_history)")
    c.add_argument("--args", help="도구 인자 JSON 또는 @파일.json")
    c.add_argument("--steps", help='여러 호출을 한 프로세스에서 차례로: [{"tool": ..., "args": {...}}] JSON 또는 @파일.json')
    c.add_argument("--operator", required=True)
    c.add_argument("--approve-as", metavar="이름", help="읽기 전용이 아닌 도구일 때 이 호출을 승인하는 사람")
    t = sub.add_parser("trace", help="Worker Activity 검색(search) / 작업 단계 상세(show) / 탐색-체크리스트 매핑(tag)")
    t.add_argument("action", choices=["search", "show", "tag"])
    t.add_argument("query", nargs="?", help="search: 검색어, show: 작업 ID, tag: 탐색 노드 ID")
    t.add_argument("--run", help="search: 제외할 현재 실행, show/tag: 실행 ID")
    t.add_argument("--tool")
    t.add_argument("--status", choices=["SKIPPED", "SUCCEEDED", "FAILED"])
    t.add_argument("--limit", type=int, default=20)
    t.add_argument("--check-id", help="tag: 이 탐색 노드가 검증하는 체크리스트 check_id (사후 매핑, 판정은 바꾸지 않음)")
    g = sub.add_parser("graph", help="자산 그래프와 탐색 그래프 요약")
    g.add_argument("run")
    rp = sub.add_parser("report", help="회사 템플릿 서식의 결과보고서 생성(COMPLETED 실행). --basic 은 내부 점검 요약")
    rp.add_argument("run")
    rp.add_argument("--type", choices=["web"], help="결과보고서 템플릿 유형(생략하면 대상 platform 으로 결정)")
    rp.add_argument("--meta", help="고객사/프로젝트/대상/일정/인력 정보(report_meta.json)")
    rp.add_argument("--findings", help="취약점 발견 내용(findings.json)")
    rp.add_argument("--basic", action="store_true", help="템플릿 없이 내부 점검 요약(Word 표 + Excel)을 만든다")
    return p


COMMANDS = {"doctor": cmd_doctor, "web": cmd_web, "migrate": cmd_migrate, "checklist": cmd_checklist, "tools": cmd_tools, "run": cmd_run, "backlog": cmd_backlog,
            "approve": cmd_approve, "retry": cmd_retry, "review": cmd_review, "finding": cmd_finding,
            "evidence": cmd_evidence, "collect": cmd_collect, "trace": cmd_trace, "graph": cmd_graph,
            "report": cmd_report}


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
