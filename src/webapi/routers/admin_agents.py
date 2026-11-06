"""웹 '에이전트' 화면: 역할별 프롬프트 조회·수정·기본값 복원(B4, UPGRADE_PLAN.md — admin.py 에서 분리).

프롬프트는 LLM 이 받는 '문구'일 뿐이다 — 도구 실행 권한·정책 검사는 프롬프트와 무관하게 policy.decide() 가 정한다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from diagnostic_workbench import prompts as wb_prompts

from ..deps import current_user, get_conn
from .admin import _audit, _need_confirm

router = APIRouter(prefix="/api/admin", tags=["admin"])


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
        wb_prompts.reset(conn, name, operator=f"web:{user}", default=default)
    else:
        wb_prompts.set(conn, name, text, operator=f"web:{user}")
    _audit(conn, user, "admin_prompt_changed", {"name": name, "before": before, "after": text})
    return {"name": name, "overridden": text != default.strip()}


@router.post("/agents/prompts/{name}/reset")
def reset_prompt(name: str, body: ConfirmBody, conn=Depends(get_conn), user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    default = _prompt_default(name)
    before = wb_prompts.get(conn, name, default)
    wb_prompts.reset(conn, name, operator=f"web:{user}", default=default)
    _audit(conn, user, "admin_prompt_reset", {"name": name, "before": before})
    return {"name": name, "overridden": False}


@router.get("/agents/prompts/{name}/versions")
def get_prompt_versions(name: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    _prompt_default(name)  # 모르는 이름이면 404
    return wb_prompts.versions(conn, name)


@router.post("/agents/prompts/{name}/versions/{version_id}/restore")
def restore_prompt_version(name: str, version_id: int, body: ConfirmBody, conn=Depends(get_conn),
                            user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    default = _prompt_default(name)
    row = wb_prompts.version(conn, name, version_id)
    if not row:
        raise HTTPException(404, "그 버전을 찾을 수 없습니다")
    before = wb_prompts.get(conn, name, default)
    text = row["text"]
    if text == default.strip():
        wb_prompts.reset(conn, name, operator=f"web:{user}", default=default)
    else:
        wb_prompts.set(conn, name, text, operator=f"web:{user}")
    _audit(conn, user, "admin_prompt_restored", {"name": name, "version_id": version_id, "before": before, "after": text})
    return {"name": name, "overridden": text != default.strip()}
