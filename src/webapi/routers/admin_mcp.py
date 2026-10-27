"""웹에서 MCP 서버 추가: 연결 테스트·도구 발견 → 운영자가 고른 도구만 등록 (artex 의 'MCP 추가 → tools/list 발견' 흐름을
우리 정책 모델에 맞게 옮긴 것).

artex 와 다른 점(CLAUDE.md §7/§8 때문에):
- 원격(sse) 서버만 추가할 수 있다. stdio 는 웹에서 받은 command 를 이 PC 에서 실행하게 되므로 웹에서 받지 않는다(파일로만).
- 주소는 루프백만(external_api.enabled 가 켜져 있을 때만 그 밖). 웹에서는 외부 허용 여부를 바꿀 수 없다.
- 도구는 '발견'만 하고 자동 등록하지 않는다. 운영자가 고른 도구만, 위험도(기본 write)와 범위 검사 인자(scope_args)를 붙여
  등록한다. 모든 도구 호출은 그대로 policy.decide() 와 scope_args 검사를 거친다.
- 인자 스키마는 서버가 알려 준 것을 저장 시점에 다시 받아 쓴다(브라우저가 보낸 값을 믿지 않는다). 우리 스키마 검증기가
  모르는 키워드가 있으면 검증이 헐거워지지 않도록 그 도구는 등록하지 않는다.
- 공유 파일(mcp_servers.json)에 있는 이름은 덮어쓰지 못한다(기존 서버의 주소를 바꿔치기하는 경로 차단).
결과는 이 PC 전용 config/mcp_servers.local.json(Git 제외)에 저장된다.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from audit import config as audit_config
from audit import schemas
from audit.policy import is_loopback_url

from ..deps import current_user, get_cfg, get_conn
from .admin import _audit, _file, _mcp_rows, _need_confirm, _write_json

router = APIRouter(prefix="/api/admin", tags=["admin"])

_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
_SCOPE_HINTS = (("url", re.compile(r"url|uri|href|link|endpoint", re.I)),
                ("host", re.compile(r"host|domain|address|\bip\b|target", re.I)),
                ("path", re.compile(r"path|file|dir|folder", re.I)))


def _check_url(cfg, url: str) -> str:
    url = url.strip()
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or len(url) > 2000:
        raise HTTPException(422, "MCP 서버 주소는 http(s)://호스트:포트/… 형식이어야 합니다(웹에서는 sse 원격 서버만 추가)")
    if not is_loopback_url(url) and not cfg.settings.external_api_enabled:
        raise HTTPException(400, "로컬(127.0.0.1/localhost) 주소만 웹에서 추가할 수 있습니다. 외부 MCP 서버는 "
                                 "config/settings.json 의 external_api.enabled 를 직접 켠 뒤 파일로 등록하세요")
    return url


def _unsupported(schema, path="$") -> list[str]:
    """우리 스키마 검증기(audit.schemas)가 모르는 키워드가 어디 있는지. 모르는 키워드가 있으면 그 도구는 호출 때마다 거부된다."""
    if not isinstance(schema, dict):
        return []
    found = [f"{path}.{k}" for k in sorted(set(schema) - schemas._SUPPORTED)]
    for k, v in (schema.get("properties") or {}).items():
        found += _unsupported(v, f"{path}.properties.{k}")
    for key in ("items", "additionalProperties"):
        found += _unsupported(schema.get(key), f"{path}.{key}")
    for key in ("anyOf", "oneOf", "allOf"):
        for i, sub in enumerate(schema.get(key) or []):
            found += _unsupported(sub, f"{path}.{key}[{i}]")
    return found


def _clean_schema(raw) -> dict:
    s = dict(raw) if isinstance(raw, dict) else {"type": "object"}
    s.pop("$schema", None)  # 순수 메타데이터라 지워도 검증은 같다
    return s


def _suggest_scope(schema: dict) -> dict[str, str]:
    """문자열 인자 중 이름이 url/host/path 같은 것을 범위 검사 인자 후보로 제안한다(운영자가 확인·수정)."""
    out = {}
    for name, sub in (schema.get("properties") or {}).items():
        if isinstance(sub, dict) and sub.get("type") == "string":
            for kind, rx in _SCOPE_HINTS:
                if rx.search(name):
                    out[name] = kind
                    break
    return out


def _discover(cfg, url: str, timeout_s: int) -> dict:
    from audit import mcp_client  # mcp 패키지를 쓰는 무거운 import 라 호출 때 가져온다
    srv = {"transport": "sse", "url": url, "timeout_s": max(5, min(int(timeout_s), 120))}
    info = mcp_client.list_tools(srv)
    tools = []
    for t in info["tools"]:
        schema = _clean_schema(t["inputSchema"])
        bad = _unsupported(schema)
        tools.append({"name": t["name"], "description": t["description"][:300], "input_schema": schema,
                      "schema_ok": not bad, "schema_error": ("지원하지 않는 스키마 키워드: " + ", ".join(bad[:5])) if bad else None,
                      "suggested_scope": _suggest_scope(schema)})
    return {"server": info["server"], "tools": tools}


class DiscoverBody(BaseModel):
    url: str
    timeout_s: int = 30


@router.post("/mcp/discover")
def discover_mcp(body: DiscoverBody, cfg=Depends(get_cfg), user: str = Depends(current_user)):
    """연결 테스트 겸 도구 발견. 아무것도 저장하지 않는다. 연결 실패는 200 으로 사유를 돌려준다(화면에 그대로 보여 주려고)."""
    url = _check_url(cfg, body.url)
    try:
        return {"ok": True, **_discover(cfg, url, body.timeout_s)}
    except Exception as e:  # 연결 거부·시간 초과·프로토콜 오류 등 어떤 실패든 사유로 보여 준다
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:500]}


class ToolSel(BaseModel):
    name: str
    risk: str = "write"
    scope_args: dict[str, str] = {}


class RegisterBody(BaseModel):
    url: str
    description: str = ""
    timeout_s: int = 60
    enabled: bool = True
    tools: list[ToolSel]
    confirm: bool = False
    confirm_unscoped_read: bool = False


@router.post("/mcp/{name}/register")
def register_mcp(name: str, body: RegisterBody, cfg=Depends(get_cfg), conn=Depends(get_conn),
                 user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    if not _NAME.match(name):
        raise HTTPException(422, "서버 이름은 영문·숫자·._- 만 쓸 수 있습니다(최대 40자)")
    shared = audit_config.load_json(_file(cfg, "mcp_servers.json"), schemas.MCP_SERVERS)["servers"]
    if name in shared:
        raise HTTPException(400, f"'{name}' 은(는) 공유 파일(mcp_servers.json)에 있는 서버라 웹에서 덮어쓸 수 없습니다")
    if not body.tools:
        raise HTTPException(422, "등록할 도구를 하나 이상 고르세요")
    url = _check_url(cfg, body.url)
    try:
        found = {t["name"]: t for t in _discover(cfg, url, body.timeout_s)["tools"]}  # 브라우저가 보낸 스키마는 쓰지 않는다
    except Exception as e:
        raise HTTPException(502, f"MCP 서버에 연결하지 못했습니다: {type(e).__name__}: {e}"[:300]) from None
    allowed, unscoped_reads = {}, []
    for sel in body.tools:
        t = found.get(sel.name)
        if t is None:
            raise HTTPException(422, f"서버에 없는 도구: {sel.name}")
        if not t["schema_ok"]:
            raise HTTPException(422, f"{sel.name}: {t['schema_error']} — 검증이 헐거워질 수 있어 등록하지 않습니다")
        if sel.risk not in ("read", "write", "dangerous"):
            raise HTTPException(422, f"{sel.name}: 위험도는 read/write/dangerous 중 하나여야 합니다")
        props = (t["input_schema"].get("properties") or {})
        for arg, kind in sel.scope_args.items():
            if kind not in ("path", "host", "url") or not (isinstance(props.get(arg), dict) and props[arg].get("type") == "string"):
                raise HTTPException(422, f"{sel.name}: 범위 검사 인자 '{arg}'({kind})가 문자열 인자가 아닙니다")
        if sel.risk == "read" and not sel.scope_args:
            unscoped_reads.append(sel.name)
        entry = {"risk": sel.risk, "args_schema": t["input_schema"]}
        if sel.scope_args:
            entry["scope_args"] = sel.scope_args
        allowed[sel.name] = entry
    if unscoped_reads and not body.confirm_unscoped_read:
        raise HTTPException(400, "범위 검사 인자가 없는 도구를 read 로 등록하면 대상 범위 확인 없이 승인 없이 실행됩니다: "
                                 + ", ".join(unscoped_reads) + " — 별도 확인(confirm_unscoped_read)이 필요합니다")
    local = _file(cfg, "mcp_servers.local.json")
    doc = audit_config.load_json(local, schemas.MCP_SERVERS) if local.exists() else {"servers": {}}
    before = doc["servers"].get(name)
    doc["servers"][name] = {"enabled": body.enabled, "transport": "sse", "url": url,
                            "timeout_s": max(5, min(body.timeout_s, 600)), "description": body.description.strip()[:300],
                            "allowed_tools": allowed}
    errs = schemas.validate(doc, schemas.MCP_SERVERS)
    if errs:
        raise HTTPException(422, "검증 실패: " + "; ".join(errs[:5]))
    _write_json(cfg, local, doc)
    _audit(conn, user, "admin_mcp_registered", {
        "server": name, "host": urlsplit(url).netloc, "enabled": body.enabled, "updated": before is not None,
        "tools": {n: {"risk": v["risk"], "scope_args": v.get("scope_args", {})} for n, v in allowed.items()}})
    return _mcp_rows(get_cfg())


class ConfirmBody(BaseModel):
    confirm: bool = False


@router.post("/mcp/{name}/delete")
def delete_mcp(name: str, body: ConfirmBody, cfg=Depends(get_cfg), conn=Depends(get_conn),
               user: str = Depends(current_user)):
    """이 PC 전용 파일에 있는 서버만 지운다(공유 파일의 서버는 끄기만 가능)."""
    _need_confirm(body.confirm)
    local = _file(cfg, "mcp_servers.local.json")
    doc = audit_config.load_json(local, schemas.MCP_SERVERS) if local.exists() else {"servers": {}}
    shared = audit_config.load_json(_file(cfg, "mcp_servers.json"), schemas.MCP_SERVERS)["servers"]
    if name not in doc["servers"]:
        raise HTTPException(404, f"이 PC 전용 서버 '{name}' 이(가) 없음")
    if name in shared:
        raise HTTPException(400, f"'{name}' 은(는) 공유 파일에도 있는 서버라 웹에서 지울 수 없습니다(끄기만 가능)")
    gone = doc["servers"].pop(name)
    _write_json(cfg, local, doc)
    _audit(conn, user, "admin_mcp_deleted", {"server": name, "host": urlsplit(gone.get("url", "")).netloc,
                                             "tools": sorted(gone.get("allowed_tools", {}))})
    return _mcp_rows(get_cfg())
