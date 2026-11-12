"""웹 '도구' 편집(B4, UPGRADE_PLAN.md — admin_extra.py 에서 분리): 끄기/켜기만. 새 도구 등록·실행 파일·인자
스키마 변경은 없다. 위험도 체계는 삭제됨(CLAUDE.md §8, 2026-11 업그레이드) — 승인 요구 여부는 인터셉트
규칙(ask)으로만 정한다. 결과는 config/tools.local.json(Git 제외)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from audit import schemas
from diagnostic_workbench import runs as wb_runs

from ..deps import current_user, get_cfg, get_conn
from .admin import _audit, _file, _need_confirm, _write_json

router = APIRouter(prefix="/api/admin", tags=["admin"])


class ToolBody(BaseModel):
    disabled: bool | None = None
    hidden_from: list[str] | None = None  # planner/worker 제안 후보에서만 숨김(실행 권한과 무관) — 생략하면 안 바꿈
    confirm: bool = False


def _tool_rows(cfg) -> dict:
    base = wb_runs.registry(cfg, apply_overrides=False)
    ov = wb_runs.tool_overrides(cfg)
    rows = [{"name": n, "read_only": t.read_only, "disabled": n in ov["disabled"],
             "hidden_from": ov["hidden_from"].get(n, []),
             "args_schema": t.args_schema, "scope_args": t.scope_args,
             "source": "mcp" if n.startswith("mcp:") else "builtin"}
            for n, t in sorted(base.items())]
    return {"tools": rows}


@router.get("/tools")
def get_tools(cfg=Depends(get_cfg), user: str = Depends(current_user)):
    return _tool_rows(cfg)


@router.put("/tools/{name:path}")
def put_tool(name: str, body: ToolBody, cfg=Depends(get_cfg), conn=Depends(get_conn), user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    base = wb_runs.registry(cfg, apply_overrides=False)
    if name not in base:
        raise HTTPException(404, f"등록되지 않은 도구: {name}")
    old = wb_runs.tool_overrides(cfg)
    new = {"disabled": list(old["disabled"]), "hidden_from": {k: list(v) for k, v in old["hidden_from"].items()}}
    if body.disabled is not None:
        new["disabled"] = [n for n in new["disabled"] if n != name] + ([name] if body.disabled else [])
    if body.hidden_from is not None:
        if body.hidden_from:
            new["hidden_from"][name] = list(body.hidden_from)
        else:
            new["hidden_from"].pop(name, None)
    errs = schemas.validate(new, schemas.TOOL_OVERRIDES)
    if errs:
        raise HTTPException(422, "검증 실패: " + "; ".join(errs[:5]))
    _write_json(cfg, _file(cfg, "tools.local.json"), new)
    _audit(conn, user, "admin_tool_changed", {"tool": name, "before": old, "after": new})
    return _tool_rows(get_cfg())
