from __future__ import annotations

import collections
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from audit.models import AuditError
from diagnostic_workbench import backlog, coordinator, db as wb_db, evidence as wb_evidence, policy as wb_policy
from diagnostic_workbench import reporting, runs as wb_runs, traces, worker

from ..deps import current_user, get_cfg, get_conn

router = APIRouter(prefix="/api/runs", tags=["runs"])

# 실행 루프(coordinator.run_loop)는 오래 걸릴 수 있어 요청-응답 주기 밖(백그라운드 스레드)에서 돌린다.
# 이 레지스트리는 이 FastAPI 프로세스 안에서만 유효하다 — 프로세스를 재시작하면 모르게 된다(로컬 단일
# 프로세스 도구이므로 허용 가능한 제한, CLI로 그 사이 'run start'를 또 돌려도 Backlog 점유(SKIP LOCKED)가
# 안전하게 막아 준다).
_LOCK = threading.Lock()
_RUNNING_LOOPS: dict[str, threading.Thread] = {}

# coordinator.run_loop 가 찍는 진행 메시지(log=print 가 원래 받는 것)를 run_id 별로 모아 둔다. 증거가
# 아니라 사람이 보는 콘솔 출력일 뿐이므로 DB 가 아니라 메모리에만 둔다(§11: 증거/감사와는 다른 것).
# _RUNNING_LOOPS 와 같은 제약: 이 프로세스 안에서만 유효, 재시작하면 비워진다.
_LOG_MAXLEN = 500
_RUN_LOGS: dict[str, collections.deque] = {}


def _append_log(run_id: str, message: str) -> None:
    with _LOCK:
        buf = _RUN_LOGS.setdefault(run_id, collections.deque(maxlen=_LOG_MAXLEN))
        buf.append({"ts": datetime.now(timezone.utc).isoformat(), "message": message})


def _make_logger(run_id: str):
    def log(*args, **kwargs):
        message = " ".join(str(a) for a in args)
        print(message, **kwargs)  # 터미널(백엔드 프로세스 stdout)에서도 그대로 보이게 유지
        _append_log(run_id, message)
    return log


def _loop_running(run_id) -> bool:
    with _LOCK:
        return str(run_id) in _RUNNING_LOOPS


def _run_loop_background(cfg, run_id: str) -> None:
    conn = wb_db.connect(cfg.dsn)
    try:
        run = wb_db.resolve_run(conn, run_id)
        coordinator.run_loop(cfg, conn, run, log=_make_logger(run_id))
    except Exception as e:
        # 백그라운드 스레드의 예외는 아무도 받지 않는다(요청-응답 주기가 이미 끝난 뒤). run_loop 이
        # RUNNING 으로 바꾸기 전에 실패하면(예: LLM 연결 불가) runs 테이블에 아무 흔적도 안 남으므로,
        # 여기서 PAUSED + 사유로 명시적으로 남겨 모니터링 화면에서 왜 멈췄는지 보이게 한다.
        _append_log(run_id, f"[web] run {run_id} 백그라운드 루프 오류: {type(e).__name__}: {e}")
        print(f"[web] run {run_id} 백그라운드 루프 오류: {type(e).__name__}: {e}")
        try:
            conn.rollback()
            coordinator._set_status(conn, run_id, "PAUSED", f"웹 실행 시작 실패: {type(e).__name__}: {e}")
        except Exception as e2:
            print(f"[web] run {run_id} 상태 기록도 실패: {type(e2).__name__}: {e2}")
    finally:
        conn.close()
        with _LOCK:
            _RUNNING_LOOPS.pop(str(run_id), None)


@router.get("")
def list_runs(conn=Depends(get_conn), user: str = Depends(current_user)):
    rows = conn.execute(
        "SELECT r.id, r.status, r.operator, r.autonomy, r.use_llm, r.created_at, r.stop_reason, "
        "r.checklist_version_id IS NOT NULL AS has_checklist, "
        "(SELECT count(*) FROM findings f WHERE f.run_id = r.id) AS findings_count "
        "FROM runs r ORDER BY r.created_at DESC LIMIT 50").fetchall()
    return rows


def _load_run(conn, run_ref: str) -> dict:
    try:
        return wb_db.resolve_run(conn, run_ref)
    except AuditError as e:
        raise HTTPException(404, str(e)) from None


class CreateRunBody(BaseModel):
    targets: list[str]
    operator: str
    checklist: str | None = None  # "checklist_id@version" 형식, 생략하면 체크리스트 없이 자율 탐색만
    use_llm: bool = False
    autonomy: str = "approval"
    tools: list[str] | None = None
    check_ids: list[str] | None = None
    max_tasks: int | None = None
    max_explorations: int | None = None
    goal: str | None = None


@router.post("")
def create_run(body: CreateRunBody, cfg=Depends(get_cfg), conn=Depends(get_conn), user: str = Depends(current_user)):
    if not body.targets:
        raise HTTPException(422, "targets 는 1개 이상이어야 함")
    cid, ver = (body.checklist.split("@", 1) if body.checklist else (None, None))
    budget_in = {k: v for k, v in (("max_tasks", body.max_tasks),
                                   ("max_explorations", body.max_explorations)) if v is not None}
    try:
        from diagnostic_workbench import config as wb_config
        budget = wb_config.validate_budget(budget_in) if budget_in else None
        run = wb_runs.create_run(conn, cfg, body.targets, body.operator, checklist_id=cid, version=ver,
                                 check_ids=body.check_ids, use_llm=body.use_llm, budget=budget,
                                 allowed_tools=body.tools, autonomy=body.autonomy, goal_text=body.goal)
    except AuditError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from None
    return run


@router.get("/{run_ref}")
def get_run(run_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    cov = reporting.coverage(conn, run["id"])
    counts = backlog.counts(conn, run["id"])
    n = conn.execute(
        "SELECT (SELECT count(*) FROM tool_invocations ti JOIN backlog_items b ON b.id=ti.backlog_item_id "
        " WHERE ti.run_id=%(r)s AND ti.status<>'SKIPPED' AND b.kind='checklist') AS tools, "
        "(SELECT count(*) FROM tool_invocations ti JOIN backlog_items b ON b.id=ti.backlog_item_id "
        " WHERE ti.run_id=%(r)s AND ti.status<>'SKIPPED' AND b.kind='exploration') AS explore_tools, "
        "(SELECT count(*) FROM evidence WHERE run_id=%(r)s AND success) AS evidence, "
        "(SELECT count(*) FROM findings WHERE run_id=%(r)s) AS findings, "
        "(SELECT count(*) FROM model_calls WHERE run_id=%(r)s) AS llm_calls",
        {"r": run["id"]}).fetchone()
    return {"run": run, "coverage": {"verdicts": cov["verdicts"], "backlog": counts}, "usage": n,
            "loop_running": _loop_running(run["id"])}


@router.post("/{run_ref}/start")
def start_run(run_ref: str, cfg=Depends(get_cfg), conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    run_id = str(run["id"])
    if run["status"] == "CANCELLED":
        raise HTTPException(400, f"실행 {run_id} 은(는) 이미 CANCELLED 상태임")
    with _LOCK:
        if run_id in _RUNNING_LOOPS:
            raise HTTPException(409, "이미 이 웹 서버에서 진행 중인 루프가 있음")
        t = threading.Thread(target=_run_loop_background, args=(cfg, run_id),
                             name=f"run-loop-{run_id[:8]}", daemon=True)
        _RUNNING_LOOPS[run_id] = t
    t.start()
    return {"started": True, "run_id": run_id}


class CancelBody(BaseModel):
    reason: str | None = None


@router.post("/{run_ref}/cancel")
def cancel_run(run_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    n = coordinator.cancel(conn, run, user)
    return {"cancelled_items": n}


@router.get("/{run_ref}/backlog")
def get_backlog(run_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    rows = conn.execute(
        "SELECT id, kind, status, reason, policy_class, blocked_reason, attempts, created_at, updated_at "
        "FROM backlog_items WHERE run_id=%s ORDER BY created_at DESC LIMIT 200", (run["id"],)).fetchall()
    return rows


def _backlog_item(conn, run, ref: str) -> dict:
    rows = conn.execute("SELECT * FROM backlog_items WHERE run_id=%s AND id::text LIKE %s",
                        (run["id"], ref.replace("%", "") + "%")).fetchall()
    if len(rows) != 1:
        raise HTTPException(404, f"작업 '{ref}' 을(를) 하나로 특정할 수 없음 ({len(rows)}건)")
    return rows[0]


class ApproveBody(BaseModel):
    reject: bool = False


@router.post("/{run_ref}/backlog/{item_ref}/approve")
def approve_backlog_item(run_ref: str, item_ref: str, body: ApproveBody,
                         cfg=Depends(get_cfg), conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    item = _backlog_item(conn, run, item_ref)
    try:
        ctx = wb_runs.build_context(cfg, conn, run)
        it, n = worker.load_item(ctx, item), 0
        for _, tool, call_args in worker.calls(it):
            d = wb_policy.decide(ctx, tool, call_args, item["asset_id"])
            if d.decision != wb_policy.ALLOW_WITH_APPROVAL:
                continue
            conn.execute("INSERT INTO approvals (run_id, asset_id, tool, args_sha256, approver, decision, "
                         "expires_at) VALUES (%s,%s,%s,%s,%s,%s, now() + make_interval(hours => %s))",
                         (run["id"], item["asset_id"], tool, wb_policy.args_hash(d.args), user,
                          "REJECTED" if body.reject else "APPROVED", cfg.approval_ttl_h))
            wb_db.audit(conn, run["id"], "approval_recorded", {"backlog_item": str(item["id"]), "tool": tool,
                                                                "args": d.args, "approver": user,
                                                                "rejected": body.reject})
            n += 1
    except AuditError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from None
    wb_db.notify_change(conn, run["id"], "approval", item["id"])
    conn.commit()
    if not n:
        raise HTTPException(400, "이 작업에는 승인 대기 중인 도구 호출이 없음")
    return {"decided": n, "rejected": body.reject}


@router.get("/{run_ref}/evidence")
def list_evidence(run_ref: str, cfg=Depends(get_cfg), conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    rows = conn.execute("SELECT * FROM evidence WHERE run_id=%s ORDER BY collected_at DESC", (run["id"],)).fetchall()
    out = []
    for e in rows:
        problem = wb_evidence.verify(e, cfg.evidence_dir)
        out.append({"id": e["id"], "tool": e["tool"], "tool_version": e["tool_version"],
                    "requirement_id": e["requirement_id"], "sha256": e["sha256"], "size": e["size"],
                    "truncated": e["truncated"], "success": e["success"], "error": e["error"],
                    "media_type": e["media_type"], "collected_at": e["collected_at"],
                    "integrity": "손상됨" if problem else ("확인됨" if e["success"] else None),
                    "integrity_detail": problem})
    return out


def _evidence_row(conn, run, evidence_ref: str) -> dict:
    row = conn.execute("SELECT * FROM evidence WHERE run_id=%s AND id::text = %s",
                       (run["id"], evidence_ref)).fetchone()
    if row is None:
        raise HTTPException(404, f"증적 '{evidence_ref}' 을(를) 찾을 수 없음")
    return row


@router.get("/{run_ref}/evidence/{evidence_ref}/download")
def download_evidence(run_ref: str, evidence_ref: str,
                      cfg=Depends(get_cfg), conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    e = _evidence_row(conn, run, evidence_ref)
    if not e["success"] or not e["rel_path"]:
        raise HTTPException(404, "이 증적에는 원본 파일이 없음(수집 실패)")
    path = cfg.evidence_dir / e["rel_path"]
    if not path.is_file():
        raise HTTPException(404, "원본 파일이 디스크에 없음")
    filename = f"{e['tool'].replace('/', '_')}_{str(e['id'])[:8]}{path.suffix}"
    return FileResponse(path, media_type=e["media_type"] or "application/octet-stream", filename=filename)


@router.get("/{run_ref}/activity")
def get_activity(run_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    return traces.get_run_trace(conn, run["id"], limit=200)


@router.get("/{run_ref}/server-log")
def get_server_log(run_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    """`run start` 가 백그라운드 스레드에서 찍는 진행 메시지(CLI 의 log=print 와 같은 것). 증거가 아니라
    사람이 보는 콘솔 출력이라 DB 가 아니라 이 프로세스 메모리에서만 보관한다(위 _RUN_LOGS 참고) — 웹
    서버를 재시작하면 비워지고, 그 사이 기록은 터미널 stdout 에 그대로 남아 있다.
    """
    run = _load_run(conn, run_ref)
    with _LOCK:
        buf = _RUN_LOGS.get(str(run["id"]))
        return list(buf) if buf else []
