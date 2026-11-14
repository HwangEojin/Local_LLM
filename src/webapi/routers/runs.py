from __future__ import annotations

import collections
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from audit.models import AuditError
from diagnostic_workbench import backlog, coordinator, db as wb_db, evidence as wb_evidence
from diagnostic_workbench import explore as wb_explore
from diagnostic_workbench import reporting, runs as wb_runs, traces, worker

from .. import deps
from ..deps import current_user, get_cfg, get_conn
from .admin import _need_confirm

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


_SORT_COLUMNS = {"created_at": "r.created_at", "status": "r.status", "operator": "r.operator"}


@router.get("")
def list_runs(conn=Depends(get_conn), user: str = Depends(current_user), q: str | None = None,
              status: str | None = None, sort: str = "created_at", order: str = "desc",
              limit: int = 20, offset: int = 0, deleted: str = "exclude", tests: str = "hide"):
    """검색(q: 실행 ID·운영자 부분 일치)·상태 필터(status: 콤마로 여러 개)·정렬·페이징.
    프론트가 모르는 값을 보내도 안전하게 기본값으로 떨어지게 한다(정렬 컬럼·방향은 화이트리스트).
    """
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    where, params = [], {}
    if q and q.strip():
        where.append("(r.run_code ILIKE %(q)s OR r.id::text ILIKE %(q)s OR r.operator ILIKE %(q)s)")
        params["q"] = f"%{q.strip()}%"
    statuses = [s.strip() for s in (status or "").split(",") if s.strip()]
    if statuses:
        where.append("r.status = ANY(%(statuses)s)")
        params["statuses"] = statuses
    # 삭제 표시된 진단: 기본은 숨김, only=삭제된 것만, all=전부(알 수 없는 값은 기본으로 떨어진다)
    if deleted == "only":
        where.append("r.deleted_at IS NOT NULL")
    elif deleted != "all":
        where.append("r.deleted_at IS NULL")
    # 테스트 진단: 운영자 이름이 'pytest' 로 시작하면 자동 테스트가 만든 진단으로 본다. 기본은 숨김(hide), show=포함, only=테스트만.
    # (파라미터를 쓰는 쿼리라 LIKE 의 % 는 %% 로 쓴다)
    if tests == "only":
        where.append("r.operator ILIKE 'pytest%%'")
    elif tests != "show":
        where.append("r.operator NOT ILIKE 'pytest%%'")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sort_col = _SORT_COLUMNS.get(sort, _SORT_COLUMNS["created_at"])
    order_sql = "ASC" if order.lower() == "asc" else "DESC"
    total = conn.execute(f"SELECT count(*) AS n FROM runs r {where_sql}", params).fetchone()["n"]
    rows = conn.execute(
        f"SELECT r.id, r.run_code, r.deleted_at, r.delete_reason, r.status, r.operator, r.autonomy, r.use_llm, r.llm_provider, r.created_at, r.stop_reason, "
        f"r.checklist_version_id IS NOT NULL AS has_checklist, "
        f"(SELECT count(*) FROM findings f WHERE f.run_id = r.id) AS findings_count "
        f"FROM runs r {where_sql} ORDER BY {sort_col} {order_sql}, r.id LIMIT %(limit)s OFFSET %(offset)s",
        {**params, "limit": limit, "offset": offset}).fetchall()
    return {"runs": rows, "total": total, "limit": limit, "offset": offset}


_load_run = deps.load_run


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
    llm_provider: str = "ollama"
    llm_profile: str | None = None
    agent_models: dict[str, str] | None = None  # 역할 -> 'ollama:<모델>' 또는 프로파일 이름
    agent_profile_names: dict[str, str] | None = None  # 역할 -> "이름 있는 프로파일"(F3a 절충안) 이름
    verifier_model: str | None = None  # G2: LLM 판정 교차 검증 모델(선택, 'ollama:<모델>' 또는 프로파일 이름)


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
                                 allowed_tools=body.tools, autonomy=body.autonomy, goal_text=body.goal,
                                 llm_provider=body.llm_provider, llm_profile=body.llm_profile,
                                 agent_models=body.agent_models, agent_profile_names=body.agent_profile_names,
                                 verifier_model=body.verifier_model)
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
    targets = [r["target_id"] for r in conn.execute(
        "SELECT target_id FROM run_assets WHERE run_id=%s ORDER BY target_id", (run["id"],))]
    # 목표 노드(여러 개면 2단계 목표 분해가 실제로 일어난 것, runs.create_run/_decompose_goal)
    goals = [{"text": g["payload"].get("text", ""), "targets": g["payload"].get("targets", [])} for g in conn.execute(
        "SELECT payload FROM exploration_nodes WHERE run_id=%s AND kind='goal' ORDER BY created_at", (run["id"],))]
    return {"run": run, "targets": targets, "coverage": {"verdicts": cov["verdicts"], "backlog": counts},
            "usage": n, "loop_running": _loop_running(run["id"]), "goals": goals}


@router.post("/{run_ref}/start")
def start_run(run_ref: str, cfg=Depends(get_cfg), conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    run_id = str(run["id"])
    if run["deleted_at"]:
        raise HTTPException(400, f"삭제된 진단은 시작할 수 없음: {run['run_code']} (먼저 복원하세요)")
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


class DeleteBody(BaseModel):
    reason: str
    confirm: bool = False


class BulkDeleteBody(DeleteBody):
    refs: list[str]


def _delete_one(conn, ref: str, user: str, reason: str) -> None:
    run = _load_run(conn, ref)
    if _loop_running(run["id"]):
        raise HTTPException(409, f"{run['run_code']}: 실행 중인 진단은 삭제할 수 없음. 먼저 취소하세요")
    try:
        wb_runs.soft_delete_run(conn, run, f"web:{user}", reason)
    except AuditError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from None


@router.post("/bulk-delete")
def bulk_delete_runs(body: BulkDeleteBody, conn=Depends(get_conn), user: str = Depends(current_user)):
    """여러 진단을 한 번에 삭제 표시한다. 일부가 실패해도 나머지는 진행하고, 건별 결과를 돌려준다."""
    if not body.confirm:
        raise HTTPException(400, "확인(confirm)이 필요한 변경입니다")
    if not body.refs or len(body.refs) > 200:
        raise HTTPException(422, "refs 는 1~200개여야 함")
    if not body.reason.strip():
        raise HTTPException(422, "삭제 사유를 입력하세요")
    deleted, skipped = [], []
    for ref in dict.fromkeys(body.refs):
        try:
            _delete_one(conn, ref, user, body.reason)
            deleted.append(ref)
        except HTTPException as e:
            skipped.append({"ref": ref, "reason": e.detail})
    return {"deleted": deleted, "skipped": skipped}


@router.post("/{run_ref}/delete")
def delete_run(run_ref: str, body: DeleteBody, conn=Depends(get_conn), user: str = Depends(current_user)):
    if not body.confirm:
        raise HTTPException(400, "확인(confirm)이 필요한 변경입니다")
    _delete_one(conn, run_ref, user, body.reason)
    return {"deleted": run_ref}


@router.post("/{run_ref}/restore")
def restore_run(run_ref: str, body: DeleteBody, conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    try:
        wb_runs.restore_run(conn, run, f"web:{user}", body.reason)
    except AuditError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from None
    return {"restored": run["run_code"]}


@router.get("/{run_ref}/backlog")
def get_backlog(run_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    rows = conn.execute(
        "SELECT id, kind, status, reason, policy_class, blocked_reason, attempts, created_at, updated_at "
        "FROM backlog_items WHERE run_id=%s ORDER BY created_at DESC LIMIT 200", (run["id"],)).fetchall()
    return rows


def _backlog_item(conn, run, ref: str) -> dict:
    try:
        return wb_db.resolve_by_prefix(conn, "backlog_items", run["id"], ref, "작업")
    except AuditError as e:
        raise HTTPException(404, str(e)) from None


class RetryBody(BaseModel):
    reason: str


@router.post("/{run_ref}/backlog/{item_ref}/retry")
def retry_backlog_item(run_ref: str, item_ref: str, body: RetryBody, conn=Depends(get_conn),
                       user: str = Depends(current_user)):
    """cli.cmd_retry 와 같은 동작: FAILED/BLOCKED 작업을 정책 판정부터 다시 받도록 PENDING 으로 되돌린다."""
    if not body.reason.strip():
        raise HTTPException(422, "재시도 사유를 입력하세요")
    run = _load_run(conn, run_ref)
    item = _backlog_item(conn, run, item_ref)
    try:
        worker.retry_item(conn, run, item, f"web:{user}", body.reason.strip())
    except AuditError as e:
        raise HTTPException(400, str(e)) from None
    conn.commit()
    return {"retried": str(item["id"])}


@router.post("/{run_ref}/backlog/retry-all")
def retry_all_blocked(run_ref: str, body: RetryBody, conn=Depends(get_conn), user: str = Depends(current_user)):
    """FAILED/BLOCKED 작업을 한 번에 재시도(레퍼런스 프로젝트 의 '막힌 의도 전체 재시도' 참고). 승인 대기 중인 작업
    (policy_class='ALLOW_WITH_APPROVAL')은 사람이 승인/거부해야 하므로 여기서 건드리지 않는다 — 승인을
    건너뛰는 경로를 만들지 않기 위함(각 작업은 worker.retry_item 을 그대로 거쳐 정책 판정부터 다시 받는다)."""
    if not body.reason.strip():
        raise HTTPException(422, "재시도 사유를 입력하세요")
    run = _load_run(conn, run_ref)
    items = conn.execute(
        "SELECT * FROM backlog_items WHERE run_id=%s AND status IN ('FAILED','BLOCKED') "
        "AND NOT (status='BLOCKED' AND policy_class='ALLOW_WITH_APPROVAL')", (run["id"],)).fetchall()
    retried = []
    for item in items:
        try:
            worker.retry_item(conn, run, item, f"web:{user}", body.reason.strip())
            retried.append(str(item["id"]))
        except AuditError:
            continue  # 상태가 그 사이 바뀐 작업은 건너뛴다(개별 재시도와 동시에 와도 안전)
    conn.commit()
    return {"retried": retried, "count": len(retried)}


class ApproveBody(BaseModel):
    reject: bool = False


@router.post("/{run_ref}/backlog/{item_ref}/approve")
def approve_backlog_item(run_ref: str, item_ref: str, body: ApproveBody,
                         cfg=Depends(get_cfg), conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    item = _backlog_item(conn, run, item_ref)
    try:
        ctx = wb_runs.build_context(cfg, conn, run)
        n = worker.decide_approvals(ctx, item, user, body.reject)
    except AuditError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from None
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


@router.get("/{run_ref}/assets")
def get_assets(run_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    """자산 그래프(이 실행의 대상 + 관찰된 관계). 웹 UI 자산 화면용 — 새 진단 로직 없음(cli.cmd_graph 와
    같은 조회를 JSON으로 바꿈)."""
    run = _load_run(conn, run_ref)
    assets = conn.execute(
        "SELECT ra.target_id, a.* FROM run_assets ra JOIN assets a ON a.id = ra.asset_id WHERE ra.run_id=%s",
        (run["id"],)).fetchall()
    edges = conn.execute(
        "SELECT s.value AS src, e.rel, d.value AS dst, e.source FROM asset_edges e "
        "JOIN assets s ON s.id = e.src_id JOIN assets d ON d.id = e.dst_id WHERE e.run_id=%s ORDER BY d.value LIMIT 200",
        (run["id"],)).fetchall()
    return {"assets": assets, "edges": edges}


@router.get("/{run_ref}/activity")
def get_activity(run_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    return traces.get_run_trace(conn, run["id"], limit=200)


@router.get("/{run_ref}/traffic")
def get_traffic(run_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    """자체 레코딩 프록시(proxy.http/proxy.https)가 남긴 요청/응답만 모은 목록.
    새 진단 로직 없음 — evidence 를 tool 기준으로 거를 뿐, 원본 내용은 기존 /evidence/.../download 로 받는다."""
    run = _load_run(conn, run_ref)
    rows = conn.execute(
        "SELECT id, tool, args, collected_at, success, size, media_type FROM evidence "
        "WHERE run_id=%s AND tool IN ('proxy.http','proxy.https') ORDER BY collected_at DESC LIMIT 500",
        (run["id"],)).fetchall()
    return rows


@router.get("/{run_ref}/questions")
def list_questions(run_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    """LLM 이 탐색 중 되물은 질문 목록(cli.cmd_question list 와 같은 조회)."""
    run = _load_run(conn, run_ref)
    rows = conn.execute(
        "SELECT id, state, payload, created_at FROM exploration_nodes WHERE run_id=%s AND kind='question' "
        "ORDER BY created_at", (run["id"],)).fetchall()
    return rows


class AnswerQuestionBody(BaseModel):
    answer: str


@router.post("/{run_ref}/questions/{question_id}/answer")
def answer_question(run_ref: str, question_id: str, body: AnswerQuestionBody,
                    conn=Depends(get_conn), user: str = Depends(current_user)):
    """cli.cmd_question answer 와 같은 동작: 답변을 기록하면 다음 계획 주기에서 LLM 이 그 답을 보고
    탐색을 이어간다(여기서 새 작업을 만들지 않음 — 그 결정은 여전히 LLM 제안 + 정책 계층을 거친다)."""
    if not body.answer.strip():
        raise HTTPException(422, "답변을 입력하세요")
    run = _load_run(conn, run_ref)
    q = conn.execute("SELECT id, payload FROM exploration_nodes WHERE run_id=%s AND kind='question' "
                     "AND id::text=%s", (run["id"], question_id)).fetchone()
    if q is None:
        raise HTTPException(404, f"질문 '{question_id}' 을(를) 찾을 수 없음")
    wb_explore.answer_question(conn, run["id"], q, body.answer, f"web:{user}")
    return {"answered": str(q["id"])}


_TRIGGER_KINDS = ("interval", "on_finding", "on_goal_met", "on_task_timeout", "on_tool_call", "on_task_create")


class TriggerBody(BaseModel):
    name: str
    condition_kind: str
    condition_args: dict = {}
    tool: str | None = None  # tool 또는 custom_agent_id 중 정확히 하나
    args: dict = {}
    custom_agent_id: int | None = None  # 있으면 발동 시 tool/args 대신 그 에이전트의 LLM 이 직접 고름
    target_asset_id: str  # backlog_items.asset_id 가 NOT NULL 이라 모든 작업처럼 대상 하나에 묶여야 함
    confirm: bool = False


@router.get("/{run_ref}/triggers")
def get_triggers(run_ref: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    return conn.execute("SELECT * FROM run_triggers WHERE run_id=%s ORDER BY created_at", (run["id"],)).fetchall()


@router.post("/{run_ref}/triggers")
def create_trigger(run_ref: str, body: TriggerBody, cfg=Depends(get_cfg), conn=Depends(get_conn),
                   user: str = Depends(current_user)):
    """Triggers(F3b-1, UPGRADE_PLAN.md): LLM 을 부르지 않는 결정론적 규칙. 여기서 만든 작업도 다른 작업과
    똑같이 backlog_items/policy.decide() 를 거친다 — 등록만으로 바로 실행되지 않는다."""
    _need_confirm(body.confirm)
    run = _load_run(conn, run_ref)
    if body.condition_kind not in _TRIGGER_KINDS:
        raise HTTPException(422, f"condition_kind 는 {list(_TRIGGER_KINDS)} 중 하나여야 함")
    if not body.name.strip():
        raise HTTPException(422, "이름을 입력하세요")
    if bool(body.tool) == bool(body.custom_agent_id):
        raise HTTPException(422, "tool 또는 custom_agent_id 중 정확히 하나를 지정하세요")
    if body.tool and body.tool not in wb_runs.registry(cfg):
        raise HTTPException(422, f"등록되지 않은 도구: {body.tool}")
    if body.custom_agent_id and not conn.execute(
            "SELECT 1 FROM custom_agents WHERE id=%s AND enabled", (body.custom_agent_id,)).fetchone():
        raise HTTPException(422, f"켜져 있는 커스텀 에이전트 {body.custom_agent_id} 를 찾을 수 없음")
    if not conn.execute("SELECT 1 FROM run_assets WHERE run_id=%s AND asset_id=%s",
                        (run["id"], body.target_asset_id)).fetchone():
        raise HTTPException(422, "이 실행의 대상이 아닌 asset_id 임")
    row = conn.execute(
        "INSERT INTO run_triggers (run_id, name, condition_kind, condition_args, tool, args, custom_agent_id, "
        "target_asset_id, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (run["id"], body.name.strip(), body.condition_kind, Jsonb(body.condition_args), body.tool,
         Jsonb(body.args), body.custom_agent_id, body.target_asset_id, f"web:{user}")).fetchone()
    wb_db.audit(conn, run["id"], "trigger_created", {"trigger_id": row["id"], "name": row["name"],
                                                     "condition_kind": row["condition_kind"], "tool": row["tool"],
                                                     "custom_agent_id": row["custom_agent_id"]})
    conn.commit()
    return row


class TriggerUpdateBody(BaseModel):
    enabled: bool | None = None
    confirm: bool = False


@router.put("/{run_ref}/triggers/{trigger_id}")
def update_trigger(run_ref: str, trigger_id: int, body: TriggerUpdateBody, conn=Depends(get_conn),
                   user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    run = _load_run(conn, run_ref)
    row = conn.execute("SELECT * FROM run_triggers WHERE run_id=%s AND id=%s", (run["id"], trigger_id)).fetchone()
    if row is None:
        raise HTTPException(404, f"트리거 {trigger_id} 를 찾을 수 없음")
    if body.enabled is not None:
        conn.execute("UPDATE run_triggers SET enabled=%s WHERE id=%s", (body.enabled, trigger_id))
        wb_db.audit(conn, run["id"], "trigger_toggled", {"trigger_id": trigger_id, "enabled": body.enabled})
    conn.commit()
    return conn.execute("SELECT * FROM run_triggers WHERE id=%s", (trigger_id,)).fetchone()


class ConfirmBody(BaseModel):
    confirm: bool = False


@router.post("/{run_ref}/triggers/{trigger_id}/delete")
def delete_trigger(run_ref: str, trigger_id: int, body: ConfirmBody, conn=Depends(get_conn),
                   user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    run = _load_run(conn, run_ref)
    n = conn.execute("DELETE FROM run_triggers WHERE run_id=%s AND id=%s", (run["id"], trigger_id)).rowcount
    if not n:
        raise HTTPException(404, f"트리거 {trigger_id} 를 찾을 수 없음")
    wb_db.audit(conn, run["id"], "trigger_deleted", {"trigger_id": trigger_id})
    conn.commit()
    return {"deleted": trigger_id}


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


# ── 결과보고서(Word/Excel, CLAUDE.md §12) — CLI `workbench report` 와 같은 reporting.generate() 를
# 웹에서 쓸 수 있게 노출. 새 테이블 없이 기존 audit_events 를 승인 기록(§10 초안/승인 구분)에 재사용한다.


class ReportBody(BaseModel):
    meta: dict | None = None  # 체크리스트 기반 실행의 --basic(kind 없음)만 meta 없이 가능
    kind: str | None = None
    confirm: bool = False


@router.post("/{run_ref}/report")
def generate_report(run_ref: str, body: ReportBody, cfg=Depends(get_cfg), conn=Depends(get_conn),
                    user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    run = _load_run(conn, run_ref)
    meta_path = None
    if body.meta is not None:
        out_dir = cfg.settings.path("reports_dir") / str(run["id"])
        out_dir.mkdir(parents=True, exist_ok=True)
        meta_path = out_dir / "_meta_web_request.json"
        meta_path.write_text(json.dumps(body.meta, ensure_ascii=False), encoding="utf-8")
    try:
        res = reporting.generate(cfg, conn, run, body.kind, meta_path, None)
    except AuditError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from None
    finally:
        if meta_path:
            meta_path.unlink(missing_ok=True)
    return res


@router.get("/{run_ref}/report")
def list_reports(run_ref: str, cfg=Depends(get_cfg), conn=Depends(get_conn), user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    approved = {r["details"]["file"]: r["details"] for r in conn.execute(
        "SELECT details FROM audit_events WHERE run_id=%s AND event='report_approved'", (run["id"],)).fetchall()}
    out_dir = cfg.settings.path("reports_dir") / str(run["id"])
    if not out_dir.is_dir():
        return []
    files = sorted(out_dir.glob("*.docx")) + sorted(out_dir.glob("*.xlsx"))
    out = []
    for p in files:
        a = approved.get(p.name)
        edited_after = None
        if a:
            edited_after = conn.execute(
                "SELECT count(*) AS n FROM finding_revisions fr JOIN findings f ON f.id = fr.finding_id "
                "WHERE f.run_id=%s AND fr.changed_at > %s", (run["id"], a["approved_at"])).fetchone()["n"]
        out.append({
            "file": p.name, "size": p.stat().st_size,
            "modified_at": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(),
            "approved_by": a.get("approved_by") if a else None,
            "approved_at": a.get("approved_at") if a else None,
            "stale": bool(edited_after),  # G3 D6: 승인 이후 finding 이 편집됐으면 재생성 필요
            "edited_after_approval": edited_after or 0,
        })
    return out


def _report_path(cfg, run, filename: str) -> Path:
    out_dir = (cfg.settings.path("reports_dir") / str(run["id"])).resolve()
    path = (out_dir / filename).resolve()
    if path.parent != out_dir or not path.is_file():
        raise HTTPException(404, "보고서 파일을 찾을 수 없음")
    return path


@router.get("/{run_ref}/report/{filename}/download")
def download_report(run_ref: str, filename: str, cfg=Depends(get_cfg), conn=Depends(get_conn),
                    user: str = Depends(current_user)):
    run = _load_run(conn, run_ref)
    path = _report_path(cfg, run, filename)
    media = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document" if path.suffix == ".docx"
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return FileResponse(path, media_type=media, filename=filename)


class ApproveReportBody(BaseModel):
    confirm: bool = False


@router.post("/{run_ref}/report/{filename}/approve")
def approve_report(run_ref: str, filename: str, body: ApproveReportBody, cfg=Depends(get_cfg),
                   conn=Depends(get_conn), user: str = Depends(current_user)):
    """CLAUDE.md §10: 자동 생성 보고서는 사람이 검토해 승인을 기록하기 전까지 초안이다."""
    _need_confirm(body.confirm)
    run = _load_run(conn, run_ref)
    _report_path(cfg, run, filename)
    now = datetime.now(timezone.utc).isoformat()
    wb_db.audit(conn, run["id"], "report_approved", {"file": filename, "approved_by": user, "approved_at": now})
    conn.commit()
    return {"file": filename, "approved_by": user, "approved_at": now}
