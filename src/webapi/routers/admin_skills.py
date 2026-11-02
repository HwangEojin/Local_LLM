"""웹 '스킬' 편집(B4, UPGRADE_PLAN.md — admin_extra.py 에서 분리): 역할 프롬프트 뒤에 붙는 운영자 지침.
도구 권한·정책은 바꾸지 못한다(prompts.get 의 role 인자)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from audit import schemas
from diagnostic_workbench import explore

from ..deps import current_user, get_conn
from .admin import _audit, _need_confirm

router = APIRouter(prefix="/api/admin", tags=["admin"])


class SkillBody(BaseModel):
    description: str = ""
    license: str = ""
    instructions: str
    roles: list[str] = []
    enabled: bool = True
    confirm: bool = False


class ConfirmBody(BaseModel):
    confirm: bool = False


def _skill_rows(conn) -> list[dict]:
    return conn.execute("SELECT name, description, license, source, instructions, roles, enabled, updated_by, "
                        "updated_at FROM agent_skills ORDER BY name").fetchall()


def _parse_skill_md(text: str) -> dict:
    """agentskills.io 형식 SKILL.md 의 frontmatter(--- ... ---)만 본다 — 평범한 key: value 와, 값을 안 쓰고
    다음 줄부터 '  - 항목' 으로 이어지는 목록만 지원한다(중첩 구조 없음, PyYAML 안 씀). frontmatter 뒤 본문이
    instructions 가 된다. scripts/ 같은 실행 파일은 이 가져오기 경로로 들어오지 않는다(텍스트만 받음)."""
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md 는 '---' 로 시작하는 frontmatter 가 있어야 합니다")
    end = text.find("\n---", 4)
    if end == -1:
        raise ValueError("frontmatter 를 닫는 '---' 를 찾을 수 없습니다")
    fm_text, body = text[4:end], text[end + 4:].lstrip("\n")
    meta: dict = {}
    cur_list_key = None
    for line in fm_text.split("\n"):
        if not line.strip():
            continue
        if line.startswith(("  - ", "  -\t")) and cur_list_key:
            meta.setdefault(cur_list_key, []).append(line.strip()[1:].strip(" -"))
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if not val:
            cur_list_key, meta[key] = key, []
        else:
            cur_list_key = None
            meta[key] = val.strip("\"'")
    return {"name": meta.get("name", ""), "description": meta.get("description", ""),
            "license": meta.get("license", ""), "roles": meta.get("roles", []), "instructions": body.strip()}


@router.get("/skills")
def get_skills(conn=Depends(get_conn), user: str = Depends(current_user)):
    return {"roles": list(explore.AGENT_ROLES), "skills": _skill_rows(conn)}


@router.put("/skills/{name}")
def put_skill(name: str, body: SkillBody, conn=Depends(get_conn), user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    if schemas.validate(name, schemas._ID):
        raise HTTPException(422, "스킬 이름은 영문·숫자·._:- 만 쓸 수 있습니다(최대 80자)")
    text = body.instructions.strip()
    if not (1 <= len(text) <= 4000):
        raise HTTPException(422, "지침은 1자 이상 4,000자 이하여야 합니다")
    bad = sorted(set(body.roles) - set(explore.AGENT_ROLES))
    if bad:
        raise HTTPException(422, f"알 수 없는 역할: {bad} (가능: {list(explore.AGENT_ROLES)})")
    before = conn.execute("SELECT instructions, roles, enabled FROM agent_skills WHERE name=%s", (name,)).fetchone()
    conn.execute(
        "INSERT INTO agent_skills (name, description, license, instructions, roles, enabled, source, updated_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,'manual',%s) ON CONFLICT (name) DO UPDATE SET description=EXCLUDED.description, "
        "license=EXCLUDED.license, instructions=EXCLUDED.instructions, roles=EXCLUDED.roles, "
        "enabled=EXCLUDED.enabled, updated_by=EXCLUDED.updated_by, updated_at=now()",
        (name, body.description.strip()[:300], body.license.strip()[:100], text, sorted(set(body.roles)),
         body.enabled, f"web:{user}"))
    _audit(conn, user, "admin_skill_changed", {"skill": name, "before": dict(before) if before else None,
                                                "after": {"instructions": text, "roles": sorted(set(body.roles)),
                                                          "enabled": body.enabled}})
    return get_skills(conn, user)


class SkillImportBody(BaseModel):
    text: str  # SKILL.md 전체 내용
    roles: list[str] | None = None  # 주면 frontmatter 의 roles 대신 이 값을 쓴다
    confirm: bool = False


@router.post("/skills/import")
def import_skill(body: SkillImportBody, conn=Depends(get_conn), user: str = Depends(current_user)):
    """SKILL.md 본문을 그대로 붙여 넣어 가져온다(ZIP 은 지원 안 함 — scripts/ 같은 실행 파일이 섞여 들어오는 경로를
    아예 만들지 않기 위해 텍스트만 받는다). 같은 이름이면 덮어쓴다."""
    _need_confirm(body.confirm)
    try:
        meta = _parse_skill_md(body.text)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    name = meta["name"].strip()
    if not name or schemas.validate(name, schemas._ID):
        raise HTTPException(422, "SKILL.md 의 name 이 비었거나 형식이 잘못됨(영문·숫자·._:- , 최대 80자)")
    roles = body.roles if body.roles is not None else meta["roles"]
    bad = sorted(set(roles) - set(explore.AGENT_ROLES))
    if bad:
        raise HTTPException(422, f"알 수 없는 역할: {bad} (가능: {list(explore.AGENT_ROLES)})")
    text = meta["instructions"]
    if not (1 <= len(text) <= 4000):
        raise HTTPException(422, "지침(frontmatter 뒤 본문)은 1자 이상 4,000자 이하여야 합니다")
    before = conn.execute("SELECT instructions, roles, enabled FROM agent_skills WHERE name=%s", (name,)).fetchone()
    conn.execute(
        "INSERT INTO agent_skills (name, description, license, instructions, roles, enabled, source, updated_by) "
        "VALUES (%s,%s,%s,%s,%s,true,'import',%s) ON CONFLICT (name) DO UPDATE SET description=EXCLUDED.description, "
        "license=EXCLUDED.license, instructions=EXCLUDED.instructions, roles=EXCLUDED.roles, source='import', "
        "updated_by=EXCLUDED.updated_by, updated_at=now()",
        (name, meta["description"][:300], meta["license"][:100], text, sorted(set(roles)), f"web:{user}"))
    _audit(conn, user, "admin_skill_imported", {"skill": name, "roles": sorted(set(roles)),
                                                "replaced": before is not None})
    return get_skills(conn, user)


@router.get("/skills/{name}/usage")
def skill_usage(name: str, conn=Depends(get_conn), user: str = Depends(current_user)):
    recent = conn.execute(
        "SELECT su.run_id, r.run_code, su.role, su.calls, su.first_at, su.last_at FROM skill_usage su "
        "LEFT JOIN runs r ON r.id = su.run_id WHERE su.skill=%s ORDER BY su.last_at DESC LIMIT 100", (name,)).fetchall()
    by_role = conn.execute("SELECT role, count(*) AS runs, sum(calls) AS calls FROM skill_usage WHERE skill=%s "
                           "GROUP BY role ORDER BY role", (name,)).fetchall()
    return {"recent": recent, "by_role": by_role}


@router.post("/skills/{name}/delete")
def delete_skill(name: str, body: ConfirmBody, conn=Depends(get_conn), user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    before = conn.execute("DELETE FROM agent_skills WHERE name=%s RETURNING instructions, roles, enabled",
                          (name,)).fetchone()
    if before is None:
        raise HTTPException(404, f"스킬 '{name}' 이(가) 없음")
    _audit(conn, user, "admin_skill_deleted", {"skill": name, "before": dict(before)})
    return get_skills(conn, user)
