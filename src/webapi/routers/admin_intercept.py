"""웹 '인터셉트' 규칙 편집(B4, UPGRADE_PLAN.md — admin_extra.py 에서 분리, admin.py 와 같은 원칙).

매치하면 자율 모드와 무관하게 ALLOW_WITH_APPROVAL 로 강제한다(policy.decide, CLAUDE.md §8) — 이 메뉴는
승인을 '추가'만 할 수 있고 거부를 끄거나 승인을 건너뛰게 만들 수 없다(그런 필드 자체가 없음). 공유 파일
(intercept_rules.json)의 규칙 이름은 웹에서 덮어쓰지 못한다 — 같은 이름으로 패턴을 좁혀 보호를 약화시키는
경로를 막기 위함(바꾸려면 파일을 직접 수정). 결과는 이 PC 전용 intercept_rules.local.json 에 저장된다.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from audit import config as audit_config
from audit import schemas

from ..deps import current_user, get_cfg, get_conn
from .admin import _audit, _file, _need_confirm, _write_json

router = APIRouter(prefix="/api/admin", tags=["admin"])


class InterceptRuleBody(BaseModel):
    tool_pattern: str
    arg_pattern: str | None = None
    enabled: bool = True
    confirm: bool = False


class TestMatchBody(BaseModel):
    tool: str
    args: dict = {}


class ConfirmBody(BaseModel):
    confirm: bool = False


def _shared_intercept_rules(cfg) -> dict[str, dict]:
    p = _file(cfg, "intercept_rules.json")
    return {r["name"]: r for r in audit_config.load_json(p, schemas.INTERCEPT_RULES)["rules"]} if p.exists() else {}


def _local_intercept_doc(cfg) -> dict:
    p = _file(cfg, "intercept_rules.local.json")
    return audit_config.load_json(p, schemas.INTERCEPT_RULES) if p.exists() else {"rules": []}


def _intercept_rows(cfg) -> list[dict]:
    merged = {n: {**r, "source": "shared"} for n, r in _shared_intercept_rules(cfg).items()}
    merged.update({r["name"]: {**r, "source": "local"} for r in _local_intercept_doc(cfg)["rules"]})
    return sorted(({**r, "enabled": r.get("enabled", True)} for r in merged.values()), key=lambda r: r["name"])


@router.get("/intercept-rules")
def get_intercept_rules(cfg=Depends(get_cfg), user: str = Depends(current_user)):
    return _intercept_rows(cfg)


@router.put("/intercept-rules/{name}")
def put_intercept_rule(name: str, body: InterceptRuleBody, cfg=Depends(get_cfg), conn=Depends(get_conn),
                       user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    if schemas.validate(name, schemas._ID):
        raise HTTPException(422, "규칙 이름은 영문·숫자·._:- 만 쓸 수 있습니다(최대 80자)")
    if name in _shared_intercept_rules(cfg):
        raise HTTPException(400, f"'{name}' 은(는) 공유 파일(intercept_rules.json)에 있는 규칙이라 웹에서 바꿀 수 없습니다")
    try:
        re.compile(body.tool_pattern)
        if body.arg_pattern:
            re.compile(body.arg_pattern)
    except re.error as e:
        raise HTTPException(422, f"정규식이 잘못됨: {e}") from None
    doc = _local_intercept_doc(cfg)
    before = next((r for r in doc["rules"] if r["name"] == name), None)
    entry = {"name": name, "tool_pattern": body.tool_pattern, "enabled": body.enabled}
    if body.arg_pattern:
        entry["arg_pattern"] = body.arg_pattern
    doc["rules"] = [r for r in doc["rules"] if r["name"] != name] + [entry]
    errs = schemas.validate(doc, schemas.INTERCEPT_RULES)
    if errs:
        raise HTTPException(422, "검증 실패: " + "; ".join(errs[:5]))
    _write_json(cfg, _file(cfg, "intercept_rules.local.json"), doc)
    _audit(conn, user, "admin_intercept_rule_changed", {"rule": name, "before": before, "after": entry})
    return _intercept_rows(get_cfg())


@router.post("/intercept-rules/{name}/delete")
def delete_intercept_rule(name: str, body: ConfirmBody, cfg=Depends(get_cfg), conn=Depends(get_conn),
                          user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    doc = _local_intercept_doc(cfg)
    if name not in {r["name"] for r in doc["rules"]}:
        raise HTTPException(404, f"이 PC 전용 규칙 '{name}' 이(가) 없음(공유 파일 규칙은 지울 수 없음)")
    doc["rules"] = [r for r in doc["rules"] if r["name"] != name]
    _write_json(cfg, _file(cfg, "intercept_rules.local.json"), doc)
    _audit(conn, user, "admin_intercept_rule_deleted", {"rule": name})
    return _intercept_rows(get_cfg())


@router.post("/intercept-rules/test-match")
def test_intercept_match(body: TestMatchBody, cfg=Depends(get_cfg), user: str = Depends(current_user)):
    """지금 적용 중인(활성) 규칙 전체를 대상으로 도구+인자 하나를 대조해 본다. 아무것도 저장하지 않는다."""
    from diagnostic_workbench import policy as wb_policy
    hit = wb_policy._intercepted(audit_config.load_intercept_rules(cfg.home), body.tool, body.args)
    return {"matched": hit is not None, "rule": hit}
