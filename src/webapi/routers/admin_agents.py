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


class RoleDefaultBody(BaseModel):
    llm_profile: str | None = None  # 비우면(None) 그 역할의 기본값 지정을 지운다
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


@router.get("/agents/role-defaults")
def get_role_defaults(conn=Depends(get_conn), user: str = Depends(current_user)):
    return wb_prompts.role_defaults(conn)


@router.put("/agents/role-defaults/{role}")
def put_role_default(role: str, body: RoleDefaultBody, conn=Depends(get_conn), user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    if role not in {r["role"] for r in wb_prompts.catalog()}:
        raise HTTPException(404, f"알 수 없는 역할: {role}")
    if body.llm_profile:
        wb_prompts.set_role_default(conn, role, body.llm_profile, operator=f"web:{user}")
    else:
        wb_prompts.clear_role_default(conn, role)
    _audit(conn, user, "admin_role_default_changed", {"role": role, "llm_profile": body.llm_profile})
    return {"role": role, "llm_profile": body.llm_profile}


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


class ProfileBody(BaseModel):
    """역할별 '이름 있는 프로파일'(F3a 절충안, UPGRADE_PLAN.md) — 고정 5역할을 유지하되 역할마다 여러 이름
    붙은 변형(예: worker-aggressive)을 둘 수 있게 한다. prompt_overrides 는 그 역할이 쓰는 프롬프트 이름 ->
    문구(지정 안 한 프롬프트는 전역 오버라이드/기본값으로 그대로 떨어짐)."""
    name: str
    prompt_overrides: dict[str, str] = {}
    llm_profile: str | None = None
    confirm: bool = False


@router.get("/agents/profiles")
def get_profiles(conn=Depends(get_conn), user: str = Depends(current_user)):
    return wb_prompts.list_profiles(conn)


@router.post("/agents/profiles/{role}")
def create_profile(role: str, body: ProfileBody, conn=Depends(get_conn), user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    if role not in {r["role"] for r in wb_prompts.catalog()}:
        raise HTTPException(404, f"알 수 없는 역할: {role}")
    if not body.name.strip():
        raise HTTPException(422, "이름을 입력하세요")
    valid_names = {p["name"] for r in wb_prompts.catalog() if r["role"] == role for p in r["prompts"]}
    unknown = sorted(set(body.prompt_overrides) - valid_names)
    if unknown:
        raise HTTPException(422, f"'{role}' 역할이 쓰지 않는 프롬프트: {unknown} (가능: {sorted(valid_names) or '없음'})")
    for pname, text in body.prompt_overrides.items():
        if not (20 <= len(text.strip()) <= 12000):
            raise HTTPException(422, f"{pname}: 프롬프트는 20자 이상 12,000자 이하여야 합니다")
    try:
        row = wb_prompts.create_profile(conn, role, body.name.strip(), body.prompt_overrides, body.llm_profile,
                                        operator=f"web:{user}")
    except Exception as e:  # UNIQUE(role, name) 충돌
        conn.rollback()
        raise HTTPException(409, f"'{role}' 역할에 '{body.name}' 프로파일이 이미 있음") from e
    _audit(conn, user, "admin_profile_created", {"role": role, "name": row["name"], "profile_id": row["id"]})
    return row


@router.post("/agents/profiles/{profile_id}/delete")
def delete_profile(profile_id: int, body: ConfirmBody, conn=Depends(get_conn), user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    if not wb_prompts.delete_profile(conn, profile_id):
        raise HTTPException(404, f"프로파일 {profile_id} 를 찾을 수 없음")
    _audit(conn, user, "admin_profile_deleted", {"profile_id": profile_id})
    return {"deleted": profile_id}
