"""웹에서 진단 환경을 제어하는 API: 진단 대상(scope), 제외·예산·제한, LLM 설정, MCP 활성화, 중지 스위치, 감사 로그.

원칙(CLAUDE.md §8): 이 모듈은 사람(로그인한 운영자)이 쓰는 경로다. LLM 은 이 API 를 부를 수 없다.
- 모든 변경은 감사 이벤트(admin_*)로 남긴다(변경 전/후 값 포함, 비밀은 db.audit 가 마스킹).
- 파일은 임시 파일에 쓴 뒤 교체(원자적)하고, 바뀌기 전 사본을 data/config_backups/ 에 보관한다.
- 웹에서 바꾸지 못하게 막는 것: 실행 파일 경로(nmap/testssl/MCP command), 외부 LLM 허용 여부, 파일 경로 설정,
  루프백이 아닌 LLM 주소. 데이터가 밖으로 나가거나 임의 명령을 실행하게 만들 수 있는 항목들이라 파일을 직접 고치게 한다.
"""
from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from audit import config as audit_config
from audit import schemas
from audit.checklist_engine import Checklists
from audit.models import AuditError, ConfigError
from audit.policy import Policy
from diagnostic_workbench import config as wb_config, db as wb_db, prompts as wb_prompts, runs as wb_runs

from .. import deps
from ..deps import current_user, get_cfg, get_conn

router = APIRouter(prefix="/api/admin", tags=["admin"])

_LOOPBACK = ("127.0.0.1", "localhost", "::1")


# ── 공통 ──
def _backup_dir(cfg) -> Path:
    d = cfg.home / "data" / "config_backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_json(cfg, path: Path, data: dict) -> None:
    """원자적 쓰기 + 직전 사본 보관. 중간에 실패해도 기존 파일은 그대로 남는다."""
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (_backup_dir(cfg) / f"{path.name}.{stamp}.bak").write_bytes(path.read_bytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    deps.reset_cfg()  # 설정을 캐시하므로 다음 요청부터 새 값을 읽게 한다


def _need_confirm(confirm: bool) -> None:
    if not confirm:
        raise HTTPException(400, "확인(confirm)이 필요한 변경입니다")


def _audit(conn, user: str, event: str, details: dict) -> None:
    wb_db.audit(conn, None, event, {"actor": f"web:{user}", **details})
    conn.commit()


def _file(cfg, name: str) -> Path:
    return cfg.home / "config" / name


# ── 진단 대상(scope) ──
class ScopeBody(BaseModel):
    scope: dict
    confirm: bool = False
    confirm_dangerous: bool = False  # 'dangerous' 위험도를 새로 허용할 때만 필요


@router.get("/scope")
def get_scope(cfg=Depends(get_cfg), user: str = Depends(current_user)):
    return audit_config.load_scope(cfg.home)


@router.put("/scope")
def put_scope(body: ScopeBody, cfg=Depends(get_cfg), conn=Depends(get_conn), user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    errs = schemas.validate(body.scope, schemas.SCOPE)
    if errs:
        raise HTTPException(422, "scope 검증 실패: " + "; ".join(errs[:10]))
    try:
        Policy(body.scope, cfg.home)  # 만료·중복 target_id·없는 경로·http(s) 아닌 url 을 여기서 걸러낸다
    except ConfigError as e:
        raise HTTPException(422, str(e)) from None
    try:
        old = audit_config.load_scope(cfg.home)
    except ConfigError:
        old = {"allowed_risks": [], "targets": []}
    if "dangerous" in body.scope["allowed_risks"] and "dangerous" not in old.get("allowed_risks", []):
        if not body.confirm_dangerous:
            raise HTTPException(400, "'dangerous' 위험도를 새로 허용하려면 별도 확인(confirm_dangerous)이 필요합니다")
    _write_json(cfg, _file(cfg, "scope.json"), body.scope)
    _audit(conn, user, "admin_scope_changed", {"before": old, "after": body.scope})
    return body.scope


# ── 제외 목록·예산·제한(workbench.json) ──
class WorkbenchBody(BaseModel):
    exclude: list[str] = []
    lease_s: int | None = None
    max_attempts: int | None = None
    approval_ttl_h: int | None = None
    min_call_interval_ms: int | None = None
    budget: dict | None = None
    confirm: bool = False


_WB_READONLY = ("nmap_path", "testssl_path")  # 실행 파일 경로는 웹에서 바꾸지 않는다


def _wb_file(cfg) -> dict:
    p = _file(cfg, "workbench.json")
    return audit_config.load_json(p, wb_config.WORKBENCH) if p.exists() else {}


@router.get("/workbench")
def get_workbench(cfg=Depends(get_cfg), user: str = Depends(current_user)):
    return {"exclude": cfg.exclude, "lease_s": cfg.lease_s, "max_attempts": cfg.max_attempts,
            "approval_ttl_h": cfg.approval_ttl_h, "min_call_interval_ms": cfg.min_call_interval_ms,
            "budget": cfg.budget, "nmap_path": cfg.nmap_path, "testssl_path": cfg.testssl_path,
            "readonly": list(_WB_READONLY)}


@router.put("/workbench")
def put_workbench(body: WorkbenchBody, cfg=Depends(get_cfg), conn=Depends(get_conn),
                  user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    old = _wb_file(cfg)
    new = {k: v for k, v in old.items() if k in _WB_READONLY}  # 읽기 전용 항목은 파일에 있는 값을 그대로 보존
    new["exclude"] = [e.strip() for e in body.exclude if e.strip()]
    for k in ("lease_s", "max_attempts", "approval_ttl_h", "min_call_interval_ms"):
        v = getattr(body, k)
        if v is not None:
            new[k] = v
    if body.budget:
        new["budget"] = body.budget
    errs = schemas.validate(new, wb_config.WORKBENCH)
    if errs:
        raise HTTPException(422, "설정 검증 실패: " + "; ".join(errs[:10]))
    _write_json(cfg, _file(cfg, "workbench.json"), new)
    _audit(conn, user, "admin_workbench_changed", {"before": old, "after": new})
    return get_workbench(deps.get_cfg(), user)


# ── LLM·제한(settings.json) ──
_OLLAMA_EDITABLE = ("model", "timeout_s", "max_request_bytes", "max_response_bytes", "max_calls_per_run",
                    "max_retries", "base_url")


class SettingsBody(BaseModel):
    ollama: dict = {}
    limits: dict = {}
    confirm: bool = False


@router.get("/settings")
def get_settings(cfg=Depends(get_cfg), user: str = Depends(current_user)):
    raw = cfg.settings.raw
    return {"ollama": raw["ollama"], "limits": raw["limits"], "external_api": raw["external_api"],
            "paths": raw["paths"], "readonly": ["external_api", "paths"]}


@router.put("/settings")
def put_settings(body: SettingsBody, cfg=Depends(get_cfg), conn=Depends(get_conn), user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    old = json.loads(json.dumps(cfg.settings.raw))
    new = json.loads(json.dumps(old))
    for k, v in body.ollama.items():
        if k not in _OLLAMA_EDITABLE:
            raise HTTPException(422, f"웹에서 바꿀 수 없는 항목: ollama.{k}")
        new["ollama"][k] = v
    for k, v in body.limits.items():
        if k not in new["limits"]:
            raise HTTPException(422, f"알 수 없는 항목: limits.{k}")
        new["limits"][k] = v
    host = (urlsplit(str(new["ollama"]["base_url"])).hostname or "").lower()
    if host not in _LOOPBACK and not new["external_api"]["enabled"]:
        raise HTTPException(422, "LLM 주소는 로컬(127.0.0.1/localhost)만 웹에서 지정할 수 있습니다. "
                                 "외부 주소는 settings.json 을 직접 수정하세요")
    errs = schemas.validate(new, schemas.SETTINGS)
    if errs:
        raise HTTPException(422, "설정 검증 실패: " + "; ".join(errs[:10]))
    _write_json(cfg, _file(cfg, "settings.json"), new)
    _audit(conn, user, "admin_settings_changed", {"before": {"ollama": old["ollama"], "limits": old["limits"]},
                                                  "after": {"ollama": new["ollama"], "limits": new["limits"]}})
    return get_settings(deps.get_cfg(), user)


# ── 중지 스위치(Kill Switch) ──
class KillBody(BaseModel):
    reason: str
    confirm: bool = False


def _kill_state(cfg) -> dict:
    f = cfg.stop_file
    if not f.exists():
        return {"active": False}
    try:
        info = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}  # CLI 등 다른 경로로 만든 빈 파일도 '켜짐'으로 본다
    return {"active": True, **info}


@router.get("/kill-switch")
def get_kill_switch(cfg=Depends(get_cfg), user: str = Depends(current_user)):
    return _kill_state(cfg)


@router.post("/kill-switch/engage")
def engage_kill_switch(body: KillBody, cfg=Depends(get_cfg), conn=Depends(get_conn),
                       user: str = Depends(current_user)):
    if not body.reason.strip():
        raise HTTPException(422, "사유를 입력하세요")
    cfg.stop_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.stop_file.write_text(json.dumps({"by": user, "at": datetime.now(timezone.utc).isoformat(),
                                         "reason": body.reason.strip()}, ensure_ascii=False), encoding="utf-8")
    _audit(conn, user, "admin_kill_switch_engaged", {"reason": body.reason.strip()})
    return _kill_state(cfg)


@router.post("/kill-switch/release")
def release_kill_switch(body: KillBody, cfg=Depends(get_cfg), conn=Depends(get_conn),
                        user: str = Depends(current_user)):
    """해제는 사람의 재승인이다(CLAUDE.md §8). 멈춘 진단은 자동으로 재개되지 않고, 운영자가 직접 '재개'해야 한다."""
    _need_confirm(body.confirm)
    if not body.reason.strip():
        raise HTTPException(422, "해제 사유를 입력하세요")
    prev = _kill_state(cfg)
    cfg.stop_file.unlink(missing_ok=True)
    _audit(conn, user, "admin_kill_switch_released", {"reason": body.reason.strip(), "was": prev})
    return _kill_state(cfg)


# ── MCP 서버 ──
def _mcp_rows(cfg) -> list[dict]:
    local = _file(cfg, "mcp_servers.local.json")
    local_names = set(audit_config.load_json(local, schemas.MCP_SERVERS)["servers"]) if local.exists() else set()
    return [{"name": n, "enabled": s["enabled"], "description": s.get("description", ""),
             "transport": s["transport"], "timeout_s": s["timeout_s"], "tools": sorted(s["allowed_tools"]),
             "source": "local" if n in local_names else "shared"}
            for n, s in audit_config.load_mcp_servers(cfg.home).items()]


@router.get("/mcp")
def get_mcp(cfg=Depends(get_cfg), user: str = Depends(current_user)):
    return _mcp_rows(cfg)


class McpBody(BaseModel):
    enabled: bool
    confirm: bool = False


@router.put("/mcp/{name}")
def put_mcp(name: str, body: McpBody, cfg=Depends(get_cfg), conn=Depends(get_conn), user: str = Depends(current_user)):
    """활성/비활성만 바꾼다(command·url·허용 도구는 건드리지 않음). 이 PC 전용 파일(.local.json, Git 제외)에 기록."""
    _need_confirm(body.confirm)
    servers = audit_config.load_mcp_servers(cfg.home)
    if name not in servers:
        raise HTTPException(404, f"MCP 서버 '{name}' 이(가) 없음")
    local = _file(cfg, "mcp_servers.local.json")
    doc = audit_config.load_json(local, schemas.MCP_SERVERS) if local.exists() else {"servers": {}}
    before = servers[name]["enabled"]
    doc["servers"][name] = {**servers[name], "enabled": body.enabled}
    _write_json(cfg, local, doc)
    _audit(conn, user, "admin_mcp_toggled", {"server": name, "before": before, "after": body.enabled})
    return _mcp_rows(deps.get_cfg())


# ── 체크리스트(조회 전용) ──
@router.get("/checklists")
def get_checklists(cfg=Depends(get_cfg), user: str = Depends(current_user)):
    cl = Checklists(cfg.settings.path("checklist_dir"))
    return [{"checklist_id": cid, "version": v["version"], "status": v["status"], "sha256": v.get("sha256")}
            for cid, v in cl.entries()]


# ── 감사 로그 ──
@router.get("/audit")
def get_audit(conn=Depends(get_conn), user: str = Depends(current_user), event: str | None = None,
              run: str | None = None, limit: int = 100, before: int | None = None):
    limit = max(1, min(limit, 500))
    where, params = [], {"limit": limit}
    if event and event.strip():
        where.append("a.event ILIKE %(event)s")
        params["event"] = f"%{event.strip()}%"
    if run and run.strip():
        try:
            params["run"] = wb_db.resolve_run(conn, run)["id"]
        except AuditError as e:
            raise HTTPException(404, str(e)) from None
        where.append("a.run_id = %(run)s")
    if before:
        where.append("a.id < %(before)s")
        params["before"] = before
    sql = ("SELECT a.id, a.ts, a.event, a.details, r.run_code FROM audit_events a LEFT JOIN runs r ON r.id = a.run_id "
           + ("WHERE " + " AND ".join(where) + " " if where else "") + "ORDER BY a.id DESC LIMIT %(limit)s")
    return conn.execute(sql, params).fetchall()


@router.get("/audit/export")
def export_audit(conn=Depends(get_conn), user: str = Depends(current_user), event: str | None = None,
                 run: str | None = None, format: str = "csv", limit: int = 5000):
    """감사 로그 내보내기. 새 진단 로직 없음 — get_audit 과 같은 필터로 audit_events 를 CSV/JSON 모양만 바꿔
    돌려준다(적용 전/후 값이 담긴 details 는 JSON 문자열로 한 칸에 넣는다)."""
    if format not in ("csv", "json"):
        raise HTTPException(422, "format 은 csv 또는 json 이어야 합니다")
    limit = max(1, min(limit, 20000))
    where, params = [], {"limit": limit}
    if event and event.strip():
        where.append("a.event ILIKE %(event)s")
        params["event"] = f"%{event.strip()}%"
    if run and run.strip():
        try:
            params["run"] = wb_db.resolve_run(conn, run)["id"]
        except AuditError as e:
            raise HTTPException(404, str(e)) from None
        where.append("a.run_id = %(run)s")
    sql = ("SELECT a.id, a.ts, a.event, a.details, r.run_code FROM audit_events a LEFT JOIN runs r ON r.id = a.run_id "
           + ("WHERE " + " AND ".join(where) + " " if where else "") + "ORDER BY a.id DESC LIMIT %(limit)s")
    rows = conn.execute(sql, params).fetchall()
    if format == "json":
        body = json.dumps([{**r, "id": r["id"], "ts": str(r["ts"])} for r in rows], ensure_ascii=False,
                          indent=2, default=str)
        return Response(body, media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="audit_log.json"'})
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    w.writerow(["id", "ts", "event", "run_code", "details"])
    for r in rows:
        w.writerow([r["id"], r["ts"], r["event"], r["run_code"] or "", json.dumps(r["details"], ensure_ascii=False, default=str)])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="audit_log.csv"'})


# ── 에이전트(역할별 프롬프트) ──
# 프롬프트는 LLM 이 받는 '문구'일 뿐이다 — 도구 실행 권한·정책 검사는 프롬프트와 무관하게 policy.decide() 가 정한다.
class PromptBody(BaseModel):
    text: str
    confirm: bool = False


class ConfirmBody(BaseModel):
    confirm: bool = False


def _prompt_default(name: str) -> str:
    for role in wb_prompts.catalog():
        for p in role["prompts"]:
            if p["name"] == name:
                return p["default"]
    raise HTTPException(404, f"편집할 수 없는 프롬프트: {name}")


@router.get("/agents")
def get_agents(conn=Depends(get_conn), user: str = Depends(current_user)):
    overrides = {r["name"]: r for r in wb_prompts.list_all(conn)}
    out = []
    for role in wb_prompts.catalog():
        prompts = []
        for p in role["prompts"]:
            o = overrides.get(p["name"])
            prompts.append({**p, "text": o["text"] if o else p["default"], "overridden": bool(o),
                            "updated_by": o["updated_by"] if o else None,
                            "updated_at": o["updated_at"] if o else None})
        out.append({"role": role["role"], "prompts": prompts})
    return out


@router.put("/agents/prompts/{name}")
def put_prompt(name: str, body: PromptBody, conn=Depends(get_conn), user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    default = _prompt_default(name)
    text = body.text.strip()
    if not (20 <= len(text) <= 12000):
        raise HTTPException(422, "프롬프트는 20자 이상 12,000자 이하여야 합니다")
    before = wb_prompts.get(conn, name, default)
    if text == default.strip():  # 기본값과 같으면 오버라이드를 남기지 않는다
        wb_prompts.reset(conn, name)
    else:
        wb_prompts.set(conn, name, text, operator=f"web:{user}")
    _audit(conn, user, "admin_prompt_changed", {"name": name, "before": before, "after": text})
    return {"name": name, "overridden": text != default.strip()}


@router.post("/agents/prompts/{name}/reset")
def reset_prompt(name: str, body: ConfirmBody, conn=Depends(get_conn), user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    default = _prompt_default(name)
    before = wb_prompts.get(conn, name, default)
    wb_prompts.reset(conn, name)
    _audit(conn, user, "admin_prompt_reset", {"name": name, "before": before})
    return {"name": name, "overridden": False}


# ── 승인(전체 진단의 승인 대기·최근 결정) ──
@router.get("/approvals")
def get_approvals(cfg=Depends(get_cfg), conn=Depends(get_conn), user: str = Depends(current_user)):
    pending = conn.execute(
        "SELECT b.id, r.run_code, b.kind, b.reason, b.payload->>'tool' AS tool, b.updated_at, a.kind AS asset_kind, "
        "a.value AS asset_value FROM backlog_items b JOIN runs r ON r.id=b.run_id JOIN assets a ON a.id=b.asset_id "
        "WHERE b.status='BLOCKED' AND b.policy_class='ALLOW_WITH_APPROVAL' AND r.deleted_at IS NULL "
        "ORDER BY b.updated_at DESC LIMIT 200").fetchall()
    recent = conn.execute(
        "SELECT ap.id, r.run_code, ap.tool, ap.decision, ap.approver, ap.decided_at, ap.expires_at, "
        "ap.expires_at < now() AS expired, a.value AS asset_value FROM approvals ap "
        "JOIN runs r ON r.id=ap.run_id JOIN assets a ON a.id=ap.asset_id ORDER BY ap.decided_at DESC LIMIT 50"
    ).fetchall()
    return {"ttl_h": cfg.approval_ttl_h, "pending": pending, "recent": recent}
