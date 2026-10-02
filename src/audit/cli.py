"""CLI. 종료 코드: 0 성공, 1 실패/거부/무효, 2 사용법 오류(argparse)."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from . import __version__, config
from .models import VERDICT_LABELS_KO, AuditError, Verdict


class Ctx:
    """명령 실행에 필요한 환경을 지연 로딩한다."""
    def __init__(self, home):
        self.home = config.resolve_home(home)
        self.settings = config.load_settings(self.home)
        self._storage = None

    @property
    def storage(self):
        if self._storage is None:
            from .storage import Storage
            self._storage = Storage(self.settings.path("db"))
        return self._storage

    def checklists(self):
        from .checklist_engine import Checklists
        return Checklists(self.settings.path("checklist_dir"))

    def policy(self):
        from .policy import Policy
        return Policy(config.load_scope(self.home), self.home)

    def registry(self):
        from .adapters import build_registry
        return build_registry(self.settings, config.load_mcp_servers(self.home))

    def evidence(self):
        from .evidence import EvidenceStore
        return EvidenceStore(self.storage, self.settings.path("evidence_dir"))


def _print_status(rows):
    worst = 0
    for status, name, detail in rows:
        print(f"[{status:4}] {name}: {detail}")
        worst = max(worst, status == "FAIL")
    return worst


def cmd_doctor(ctx_home, args):
    rows = []
    py = sys.version_info
    rows.append(("OK" if py >= (3, 12) else "FAIL", "파이썬", sys.version.split()[0]))
    try:
        ctx = Ctx(ctx_home)
        rows.append(("OK", "설정", str(ctx.home / "config" / "settings.json")))
    except AuditError as e:
        rows.append(("FAIL", "설정", str(e)))
        return _print_status(rows)
    for name, fn in (("진단 범위(scope)", lambda: f"대상 {len(ctx.policy().targets)}개"),
                     ("데이터베이스", lambda: str(ctx.settings.path("db")) if ctx.storage else ""),
                     ("MCP 설정", lambda: f"서버 {len(config.load_mcp_servers(ctx.home))}개")):
        try:
            rows.append(("OK", name, fn()))
        except Exception as e:
            rows.append(("FAIL", name, str(e)))
    try:
        cl = ctx.checklists()
        for cid, v in cl.entries():
            errs = cl.check_version(cid, v["version"])
            rows.append(("FAIL" if errs and v["status"] == "active" else "WARN" if errs else "OK",
                         f"체크리스트 {cid}@{v['version']} [{v['status']}]", "; ".join(errs) or "유효"))
    except AuditError as e:
        rows.append(("FAIL", "체크리스트 manifest", str(e)))
    from importlib.metadata import PackageNotFoundError, version
    for pkg in ("mcp", "python-docx", "openpyxl"):
        try:
            rows.append(("OK", f"패키지 {pkg}", version(pkg)))
        except PackageNotFoundError:
            rows.append(("FAIL", f"패키지 {pkg}", "설치되지 않음"))
    from .local_model import LLMError, OllamaClient
    try:
        info = OllamaClient(ctx.settings.ollama, ctx.settings.external_api_enabled).check_ready()
        rows.append(("OK", "ollama", f"v{info['version']} 모델={info['model']} capabilities={info['capabilities']}"))
    except (LLMError, AuditError) as e:
        rows.append(("WARN", "ollama", f"{e} — use_llm=true 인 계획은 실행이 거부됩니다"))
    try:
        servers = config.load_mcp_servers(ctx.home)
        for name, srv in servers.items():
            if not srv["enabled"]:
                rows.append(("INFO", f"MCP {name}", "비활성"))
            elif args.probe_mcp:
                from . import mcp_client
                try:
                    info = mcp_client.list_tools(srv)
                    rows.append(("OK", f"MCP {name}", f"연결됨 {info['server']}, 도구 {len(info['tools'])}개"))
                except AuditError as e:
                    rows.append(("FAIL", f"MCP {name}", str(e)))
            else:
                rows.append(("INFO", f"MCP {name}", "활성, 연결 확인 안 함 (--probe-mcp 사용)"))
    except AuditError:
        pass
    return _print_status(rows)


def cmd_checklist(ctx, args):
    cl = ctx.checklists()
    if args.action == "list":
        for cid, v in cl.entries():
            print(f"{cid}\t{v['version']}\t{v['status']}\t{v.get('sha256', '-')[:12]}")
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
    if args.action == "import":
        return _import_checklists(ctx, args)
    if args.action == "activate":
        if not (args.checklist_id and args.version):
            print("activate 에는 <checklist_id> <version> 이 필요합니다", file=sys.stderr)
            return 2
        sha = cl.activate(args.checklist_id, args.version)
        ctx.storage.audit(None, "checklist_activated", {"checklist_id": args.checklist_id,
                                                        "version": args.version, "sha256": sha})
        print(f"활성화됨: {args.checklist_id}@{args.version} sha256={sha}")
        return 0


def _import_checklists(ctx, args):
    from . import importer
    xlsx = Path(args.checklist_id) if args.checklist_id else None
    if xlsx is None or not xlsx.is_file():
        print("import 에는 원본 xlsx 경로가 필요합니다: checklist import <xlsx> [version] [--pdf <pdf>]",
              file=sys.stderr)
        return 2
    pdf = Path(args.pdf) if args.pdf else None
    if pdf is not None and not pdf.is_file():
        raise AuditError(f"PDF 파일을 찾을 수 없음: {pdf}")
    checklists, notes = importer.convert(xlsx, version=args.version or "2026-1", pdf=pdf)
    sources = [xlsx] + ([pdf] if pdf else [])
    written = importer.write_checklists(checklists, ctx.settings.path("checklist_dir"),
                                        args.version or "2026-1", sources, replace_draft=args.replace_draft)
    ctx.storage.audit(None, "checklist_imported", {"source": xlsx.name, "sha256": importer.sha256_file(xlsx),
                                                   "checklists": list(checklists)})
    total = sum(len(c["items"]) for c in checklists.values())
    incomplete = sum(1 for c in checklists.values() for i in c["items"] if i["item_status"] != "complete")
    print(f"변환 완료: 체크리스트 {len(written)}개, 항목 {total}개 (기준 미확보 incomplete {incomplete}개)")
    for cid, c in checklists.items():
        n_inc = sum(1 for i in c["items"] if i["item_status"] != "complete")
        print(f"  {cid:30} 항목 {len(c['items']):4}  incomplete {n_inc:4}")
    linked = sum(1 for c in checklists.values() for i in c["items"] if "주통 가이드 위치" in i.get("source_data", {}))
    if pdf:
        print(f"PDF 쪽 번호 연결: 항목 {linked}개. 항목명 표기 차이 {len({n for n in notes if n.startswith('(PDF 연결)')})}건")
    print("상태는 모두 draft 입니다. 검증 후 활성화하세요: checklist validate / checklist activate <id> <version>")
    return 0


def _resolve(ctx, plan_path):
    from .planner import validate_plan
    plan = config.load_json(Path(plan_path))
    policy, registry = ctx.policy(), ctx.registry()
    return validate_plan(plan, policy, ctx.checklists(), registry), policy, registry


def cmd_plan(ctx, args):
    resolved, _, _ = _resolve(ctx, args.plan)
    print(f"계획 {resolved['plan']['plan_id']} 정상: 작업 {len(resolved['tasks'])}건, "
          f"도구 호출 {len(resolved['expected_calls'])}건")
    return 0


def cmd_run(ctx, args):
    if args.resume:
        run = ctx.storage.get_run(args.resume)
        if run is None:
            raise AuditError(f"알 수 없는 실행 {args.resume}")
        from .planner import validate_plan
        resolved = validate_plan(json.loads(run["plan_json"]), ctx.policy(), ctx.checklists(), ctx.registry())
        policy, registry = ctx.policy(), ctx.registry()
    else:
        if not args.plan:
            print("run 에는 <plan.json> 또는 --resume RUN_ID 가 필요합니다", file=sys.stderr)
            return 2
        resolved, policy, registry = _resolve(ctx, args.plan)
    plan = resolved["plan"]
    if args.dry_run:
        print(f"[모의 실행] 계획={plan['plan_id']} 체크리스트={plan['checklist_id']}@{plan['checklist_version']}")
        print(f"작업: {len(resolved['tasks'])}건  use_llm: {plan.get('use_llm', False)}")
        for c in resolved["expected_calls"]:
            print(f"  {c['check_id']:<12} {c['target_id']:<14} {c['tool']:<22} {c['decision']:<15} "
                  f"{json.dumps(c['args'], ensure_ascii=False)}")
        ctx.storage.audit(None, "dry_run", {"plan_id": plan["plan_id"],
                                            "calls": len(resolved["expected_calls"])})
        return 0
    llm = None
    if plan.get("use_llm"):
        from .local_model import OllamaClient
        llm = OllamaClient(ctx.settings.ollama, ctx.settings.external_api_enabled, storage=ctx.storage)
        llm.check_ready()  # fail-closed: 규칙 전용으로 조용히 전환하지 않는다
    from .orchestrator import Orchestrator
    from .policy import interactive_approver
    orch = Orchestrator(ctx.storage, ctx.evidence(), policy, registry, ctx.settings, llm=llm,
                        approver=interactive_approver)
    if llm:  # run_id가 생성되면 모델 호출을 해당 실행에 귀속한다
        orig = orch.execute

        def execute(run_id, r):
            llm.run_id = run_id
            return orig(run_id, r)
        orch.execute = execute
    run_id = orch.resume(args.resume, resolved) if args.resume else orch.start(resolved)
    print(f"실행 {run_id} 완료")
    return _print_results(ctx, run_id)


def _print_results(ctx, run_id):
    run = ctx.storage.get_run(run_id)
    if run is None:
        raise AuditError(f"알 수 없는 실행 {run_id}")
    rows = ctx.storage.results(run_id)
    print(f"실행 {run_id} 상태={run['status']} 체크리스트={run['checklist_id']}@{run['checklist_version']}"
          + (f" 오류={run['error']}" if run["error"] else ""))
    for r in rows:
        print(f"  {r['check_id']:<12} {r['target_id']:<14} {r['verdict']:<15} {r['reason']}")
    c = Counter(r["verdict"] for r in rows)
    print("요약: " + ", ".join(f"{v.value}({VERDICT_LABELS_KO[v]})={c.get(v.value, 0)}" for v in Verdict))
    open_items = c.get("MANUAL_REVIEW", 0) + c.get("INCONCLUSIVE", 0)
    if open_items:
        print(f"미확정 항목 {open_items}건: 수동확인/진단불가 — 'audit review' 로 검토 결과를 기록하세요")
    calls = ctx.storage.model_calls(run_id)
    if calls:
        rep = [x for x in calls if x["token_source"] == "reported"]
        print(f"LLM 호출: {len(calls)}회 (성공 {sum(x['ok'] for x in calls)}회); API 보고 토큰 "
              f"입력={sum(x['prompt_tokens'] or 0 for x in rep)} 출력={sum(x['completion_tokens'] or 0 for x in rep)}; "
              f"토큰 정보 없는 호출: {len(calls) - len(rep)}회")
    return 0


def cmd_results(ctx, args):
    return _print_results(ctx, args.run_id)


def cmd_review(ctx, args):
    r = ctx.storage.get_result(args.run_id, args.check_id, args.target_id)
    if r is None:
        raise AuditError("해당 결과가 없음")
    new = dict(r)
    new.update(verdict=args.verdict, method="manual_review",
               reason=f"수동 검토({args.reviewer}): {args.reason}")
    ctx.storage.save_result(new, actor=f"reviewer:{args.reviewer}")
    ctx.storage.audit(args.run_id, "verdict_changed", {"check_id": args.check_id, "target_id": args.target_id,
                                                       "old": r["verdict"], "new": args.verdict,
                                                       "reviewer": args.reviewer})
    print(f"{args.check_id}/{args.target_id}: {r['verdict']} -> {args.verdict} (이력 기록됨)")
    return 0


def _pentest_report(ctx, args):
    from . import pentest_report
    if not args.meta:
        print("--type 사용 시 --meta <report_meta.json> 이 필요합니다", file=sys.stderr)
        return 2
    res = pentest_report.generate(ctx.storage, args.run_id, ctx.checklists(), ctx.settings, args.type,
                                  args.meta, args.findings)
    print(f"보고서: {res['docx']}\n템플릿: {res['template']}  취약점(FAIL) {res['findings']}건")
    for w in res["warnings"]:
        print(f"  경고: {w}")
    if res["problems"]:
        print("보고서 검증 실패:")
        for p in res["problems"]:
            print(f"  - {p}")
        return 1
    print("검증 완료: 파일을 다시 열어 모든 취약점이 반영되었음을 확인했습니다. "
          "Word 에서 열 때 '필드 업데이트' 안내가 나오면 '예'를 선택하세요(목차/번호 갱신).")
    return 0


def cmd_report(ctx, args):
    if args.type:
        return _pentest_report(ctx, args)
    from . import report
    res = report.generate(ctx.storage, args.run_id, ctx.checklists(), ctx.settings, ctx.evidence())
    print(f"docx: {res['docx']}\nxlsx: {res['xlsx']}")
    print("집계: " + json.dumps(res["counts"]))
    if res["problems"]:
        print("보고서 검증 실패:")
        for p in res["problems"]:
            print(f"  - {p}")
        return 1
    print("검증 완료: 두 파일을 다시 열어 판정, 집계, 증거 해시가 DB와 일치함을 확인했습니다")
    return 0


def cmd_evidence(ctx, args):
    if args.action == "list":
        rows = ctx.storage.evidence(args.run_id)
        if not rows:
            print("증거 없음")
        for e in rows:
            state = "성공" if e["success"] else f"실패: {e['error']}"
            print(f"{e['evidence_id']}  {e['check_id']:<14} {e['target_id']:<12} {e['tool']:<34} "
                  f"{(e['sha256'] or '-')[:12]}  {e['size'] if e['size'] is not None else '-':>7}B  {state}")
        return 0
    from .manual_evidence import add_tool_evidence
    from .policy import interactive_approver
    try:
        tool_args = json.loads(args.args) if args.args else {}
    except json.JSONDecodeError as e:
        print(f"--args 가 JSON 이 아닙니다: {e}", file=sys.stderr)
        return 2
    res = add_tool_evidence(ctx.storage, ctx.evidence(), ctx.policy(), ctx.registry(), ctx.settings,
                            interactive_approver, args.run_id, args.check_id, args.target_id, args.tool, tool_args)
    print(f"증거 추가됨: {res['evidence_id']}")
    sf = res["scope_filter"]
    if sf:
        print(f"  범위 필터: 수신 {sf['received']}건 → 저장 {sf['kept']}건 (범위 밖 제외 {sf['dropped_out_of_scope']}건, "
              f"호스트 불명 제외 {sf['dropped_unknown_host']}건; 허용 호스트 {', '.join(sf['allowed_hosts'])})")
        if sf["received"] == 0:
            print("  참고: 도구가 항목을 돌려주지 않았습니다(Burp 에 이력/이슈가 없거나 offset 이 끝을 넘음).")
        elif sf["kept"] == 0 and sf["dropped_unknown_host"] > 0:
            print("  경고: 요청 호스트를 판별하지 못해 모두 제외되었습니다. 출력 형식이 예상과 다를 수 있으니 "
                  "adapters/burp.py 의 호스트 판별 규칙을 확인하세요.")
    return 0


def cmd_mcp(ctx, args):
    servers = config.load_mcp_servers(ctx.home)
    rc = 0
    for name, srv in servers.items():
        where = srv.get("url") or " ".join([srv["command"], *srv.get("args", [])])
        print(f"{name}  [{'활성' if srv['enabled'] else '비활성'}]  {srv['transport']}  {where}")
        for tool, pol in srv["allowed_tools"].items():
            print(f"    허용 {tool:<28} 위험도={pol['risk']}"
                  + (f" 범위검사_인자={pol['scope_args']}" if pol.get("scope_args") else ""))
        if args.probe and srv["enabled"]:
            from . import mcp_client
            try:
                info = mcp_client.list_tools(srv)
                offered = {t["name"] for t in info["tools"]}
                print(f"    연결됨: {info['server']}; 서버 제공 도구 {len(offered)}개")
                for tool in srv["allowed_tools"]:
                    if tool not in offered:
                        print(f"    경고: 허용 목록의 도구 '{tool}' 을(를) 서버가 제공하지 않음")
            except AuditError as e:
                print(f"    연결 실패: {e}")
                rc = 1
    return rc


def build_parser():
    p = argparse.ArgumentParser(prog="audit", description="Local Audit Workstation (로컬 진단 워크스테이션)")
    p.add_argument("--home", help="프로젝트 디렉터리 (기본값: $AUDIT_HOME 또는 현재 디렉터리)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor", help="Python, Ollama, 설정, DB, MCP 상태를 점검")
    d.add_argument("--probe-mcp", action="store_true", help="활성화된 MCP 서버에 실제로 연결해 확인")
    c = sub.add_parser("checklist", help="체크리스트 목록 조회/검증/활성화/원본 xlsx 가져오기")
    c.add_argument("action", choices=["list", "validate", "activate", "import"])
    c.add_argument("checklist_id", nargs="?")
    c.add_argument("version", nargs="?")
    c.add_argument("--pdf", help="import 시 쪽 번호를 연결할 참고 PDF 경로(pdftotext 필요)")
    c.add_argument("--replace-draft", action="store_true", help="import 시 같은 버전이 draft 이면 교체")
    pl = sub.add_parser("plan", help="진단 계획 검증")
    pl.add_argument("plan")
    r = sub.add_parser("run", help="진단 계획 실행")
    r.add_argument("plan", nargs="?")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--resume", metavar="RUN_ID")
    rp = sub.add_parser("report", help="Word + Excel 보고서 생성")
    rp.add_argument("run_id")
    rp.add_argument("--type", choices=["web"], help="모의해킹 결과보고서 템플릿 유형(생략하면 기본 점검 보고서)")
    rp.add_argument("--meta", help="--type 사용 시 고객사/프로젝트/대상/일정/인력 정보(report_meta.json)")
    rp.add_argument("--findings", help="--type 사용 시 취약점 발견 내용(findings.json)")
    rs = sub.add_parser("results", help="진단 결과 조회")
    rs.add_argument("run_id")
    rv = sub.add_parser("review", help="사람이 검토한 판정을 기록(변경 이력 보존)")
    rv.add_argument("run_id")
    rv.add_argument("check_id")
    rv.add_argument("target_id")
    rv.add_argument("verdict", choices=[v.value for v in Verdict])
    rv.add_argument("--reason", required=True)
    rv.add_argument("--reviewer", required=True)
    ev = sub.add_parser("evidence", help="실행에 증거 추가/조회 (예: Burp 프록시 이력, 스캐너 이슈)")
    ev.add_argument("action", choices=["add", "list"])
    ev.add_argument("run_id")
    ev.add_argument("check_id", nargs="?")
    ev.add_argument("target_id", nargs="?")
    ev.add_argument("tool", nargs="?", help="등록된 도구 이름 (예: mcp:burp/get_scanner_issues)")
    ev.add_argument("--args", help="도구 인자 JSON (예: '{\"count\": 20, \"offset\": 0}')")
    m = sub.add_parser("mcp", help="MCP 서버 조회")
    m.add_argument("action", choices=["list"])
    m.add_argument("--probe", action="store_true", help="연결하여 서버가 제공하는 도구 목록 확인")
    return p


COMMANDS = {"evidence": cmd_evidence, "checklist": cmd_checklist, "plan": cmd_plan, "run": cmd_run, "report": cmd_report,
            "results": cmd_results, "review": cmd_review, "mcp": cmd_mcp}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # 구형 Windows 콘솔에서 한글 출력
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    if args.cmd == "evidence" and args.action == "add" and not (args.check_id and args.target_id and args.tool):
        print("evidence add 에는 <run_id> <check_id> <target_id> <tool> 이 필요합니다", file=sys.stderr)
        return 2
    ctx = None
    try:
        if args.cmd == "doctor":
            return cmd_doctor(args.home, args)
        ctx = Ctx(args.home)
        return COMMANDS[args.cmd](ctx, args)
    except KeyboardInterrupt:
        print("중단되었습니다. 이어서 실행: python -m audit run --resume <run_id>", file=sys.stderr)
        return 1
    except AuditError as e:
        print(f"오류: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        if ctx is not None and ctx._storage is not None:
            ctx._storage.close()
