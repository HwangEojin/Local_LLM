"""웹 '도구' 편집, '알림', '스킬' API (admin.py 와 같은 원칙: 사람이 쓰는 경로, 모든 변경은 감사 이벤트).

- 도구: 끄기와 위험도 변경만. 새 도구 등록·실행 파일·인자 스키마 변경은 없다. 위험도를 '낮추는' 것은 승인·범위
  통제를 약하게 만드는 일이라 별도 확인(confirm_lower_risk)이 있어야 한다. 결과는 config/tools.local.json(Git 제외).
- 알림: 웹훅 주소는 데이터가 밖으로 나가는 통로다. 주소는 응답에 마스킹해서 내보내고(웹훅 주소 자체가 비밀),
  루프백/사설 주소가 아닌 곳을 켜거나 새로 등록하려면 별도 확인(confirm_external)이 있어야 한다.
  결과는 config/notify.local.json(Git 제외). 공유 파일(notify.json)의 채널은 끄고 켜기만 덮어쓴다.
- 스킬: 역할 프롬프트 뒤에 붙는 운영자 지침. 도구 권한·정책은 바꾸지 못한다(prompts.get 의 role 인자).
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from audit import config as audit_config
from audit import schemas
from diagnostic_workbench import explore, notify, runs as wb_runs

from ..deps import current_user, get_cfg, get_conn
from .admin import _audit, _file, _need_confirm, _write_json

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ── 도구 편집 ──
class ToolBody(BaseModel):
    disabled: bool | None = None
    risk: str | None = None  # 원하는 '실제' 위험도. 기본 위험도와 같으면 오버라이드를 지운다
    confirm: bool = False
    confirm_lower_risk: bool = False


def _tool_rows(cfg) -> dict:
    base = wb_runs.registry(cfg, apply_overrides=False)
    ov = wb_runs.tool_overrides(cfg)
    allowed = set(audit_config.load_scope(cfg.home).get("allowed_risks", []))
    rows = []
    for n, t in sorted(base.items()):
        risk = ov["risk"].get(n, t.risk)
        rows.append({"name": n, "risk": risk, "base_risk": t.risk, "disabled": n in ov["disabled"],
                     "args_schema": t.args_schema, "scope_args": t.scope_args,
                     "source": "mcp" if n.startswith("mcp:") else "builtin", "allowed_by_scope": risk in allowed})
    return {"allowed_risks": sorted(allowed), "tools": rows}


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
    new = {"disabled": list(old["disabled"]), "risk": dict(old["risk"])}
    if body.disabled is not None:
        new["disabled"] = [n for n in new["disabled"] if n != name] + ([name] if body.disabled else [])
    if body.risk is not None:
        if body.risk not in wb_runs.RISK_ORDER:
            raise HTTPException(422, f"위험도는 {list(wb_runs.RISK_ORDER)} 중 하나여야 합니다")
        current = old["risk"].get(name, base[name].risk)
        if wb_runs.RISK_ORDER[body.risk] < wb_runs.RISK_ORDER[current] and not body.confirm_lower_risk:
            raise HTTPException(400, "위험도를 낮추면 승인·범위 통제가 약해집니다. 별도 확인(confirm_lower_risk)이 필요합니다")
        if body.risk == base[name].risk:
            new["risk"].pop(name, None)
        else:
            new["risk"][name] = body.risk
    errs = schemas.validate(new, schemas.TOOL_OVERRIDES)
    if errs:
        raise HTTPException(422, "검증 실패: " + "; ".join(errs[:5]))
    _write_json(cfg, _file(cfg, "tools.local.json"), new)
    _audit(conn, user, "admin_tool_changed", {"tool": name, "before": old, "after": new})
    return _tool_rows(get_cfg())


# ── 알림 ──
class ChannelBody(BaseModel):
    url: str | None = None
    enabled: bool | None = None
    min_severity: str | None = None  # "" 를 보내면 필터 지움(전부 보냄), None 이면 그대로 둠
    confirm: bool = False
    confirm_external: bool = False


class ConfirmBody(BaseModel):
    confirm: bool = False


def _is_internal(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if host in ("localhost", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _mask(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}/…(숨김)" if p.path.strip("/") or p.query else f"{p.scheme}://{p.netloc}"


def _check_url(url: str) -> str:
    url = url.strip()
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or len(url) > 2000:
        raise HTTPException(422, "웹훅 주소는 http(s)://호스트/… 형식이어야 합니다(최대 2000자)")
    return url


def _channel_rows(cfg) -> list[dict]:
    return [{"name": c["name"], "kind": c["kind"], "enabled": c.get("enabled", True), "source": c["source"],
             "url_masked": _mask(c["url"]), "external": not _is_internal(c["url"]),
             "min_severity": c.get("min_severity")}
            for c in notify.load_all_channels(_file(cfg, "notify.json"))]


def _local_doc(cfg) -> dict:
    p = _file(cfg, "notify.local.json")
    return audit_config.load_json(p, schemas.NOTIFY_CHANNELS) if p.exists() else {"channels": []}


@router.get("/notifications")
def get_notifications(cfg=Depends(get_cfg), user: str = Depends(current_user)):
    return _channel_rows(cfg)


@router.put("/notifications/{name}")
def put_notification(name: str, body: ChannelBody, cfg=Depends(get_cfg), conn=Depends(get_conn),
                     user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    if schemas.validate(name, schemas._ID):
        raise HTTPException(422, "채널 이름은 영문·숫자·._:- 만 쓸 수 있습니다(최대 80자)")
    all_ch = {c["name"]: c for c in notify.load_all_channels(_file(cfg, "notify.json"))}
    cur = all_ch.get(name)
    url = _check_url(body.url) if body.url is not None else (cur["url"] if cur else None)
    if url is None:
        raise HTTPException(422, "새 채널에는 url 이 필요합니다")
    if cur and cur["source"] == "shared" and body.url is not None and body.url.strip() != cur["url"]:
        raise HTTPException(400, "공유 파일(notify.json)의 채널 주소는 웹에서 바꿀 수 없습니다. 끄고 켜기만 가능합니다")
    enabled = body.enabled if body.enabled is not None else (cur.get("enabled", True) if cur else True)
    changes_target = (cur is None) or (body.url is not None and url != cur["url"]) or (enabled and not cur.get("enabled", True))
    if enabled and changes_target and not _is_internal(url) and not body.confirm_external:
        raise HTTPException(400, "외부 주소로 진단 결과 알림이 전송됩니다. 별도 확인(confirm_external)이 필요합니다")
    min_severity = (cur or {}).get("min_severity") if body.min_severity is None else (body.min_severity or None)
    entry = {"name": name, "kind": "webhook", "url": url, "enabled": enabled}
    if min_severity:
        entry["min_severity"] = min_severity
    doc = _local_doc(cfg)
    doc["channels"] = [c for c in doc["channels"] if c["name"] != name] + [entry]
    errs = schemas.validate(doc, schemas.NOTIFY_CHANNELS)
    if errs:
        raise HTTPException(422, "검증 실패: " + "; ".join(errs[:5]))
    _write_json(cfg, _file(cfg, "notify.local.json"), doc)
    # 주소 자체는 비밀이라 감사 로그에는 마스킹한 값만 남긴다
    _audit(conn, user, "admin_notification_changed", {"channel": name, "enabled": enabled, "url": _mask(url),
                                                       "min_severity": min_severity, "created": cur is None})
    return _channel_rows(get_cfg())


@router.get("/notifications/history")
def notification_history(limit: int = 100, conn=Depends(get_conn), user: str = Depends(current_user)):
    """최근 전송 이력(audit_events 의 notification_sent/notification_failed). 새 테이블 없음 — 이미 §8 감사
    로그에 남는 기록을 전송 이력 형태로만 보여준다."""
    limit = max(1, min(limit, 500))
    return conn.execute(
        "SELECT a.id, a.ts, a.event, a.details, r.run_code FROM audit_events a LEFT JOIN runs r ON r.id = a.run_id "
        "WHERE a.event IN ('notification_sent','notification_failed') ORDER BY a.id DESC LIMIT %s",
        (limit,)).fetchall()


@router.post("/notifications/{name}/delete")
def delete_notification(name: str, body: ConfirmBody, cfg=Depends(get_cfg), conn=Depends(get_conn),
                        user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    doc = _local_doc(cfg)
    if name not in {c["name"] for c in doc["channels"]}:
        raise HTTPException(404, "이 PC 전용 채널만 삭제할 수 있습니다(공유 파일의 채널은 끄기만 가능)")
    doc["channels"] = [c for c in doc["channels"] if c["name"] != name]
    _write_json(cfg, _file(cfg, "notify.local.json"), doc)
    _audit(conn, user, "admin_notification_deleted", {"channel": name})
    return _channel_rows(get_cfg())


@router.post("/notifications/{name}/test")
def test_notification(name: str, body: ConfirmBody, cfg=Depends(get_cfg), conn=Depends(get_conn),
                      user: str = Depends(current_user)):
    """저장된 주소로 테스트 이벤트 한 번 전송(진단 데이터 없음). 성공·실패 모두 200 으로 사유를 돌려준다."""
    _need_confirm(body.confirm)
    ch = {c["name"]: c for c in notify.load_all_channels(_file(cfg, "notify.json"))}.get(name)
    if ch is None:
        raise HTTPException(404, f"채널 '{name}' 이(가) 없음")
    detail, ok = notify.send_test(ch["url"])
    _audit(conn, user, "admin_notification_tested", {"channel": name, "ok": ok, "detail": detail})
    return {"ok": ok, "detail": detail}


# ── 스킬 ──
class SkillBody(BaseModel):
    description: str = ""
    license: str = ""
    instructions: str
    roles: list[str] = []
    enabled: bool = True
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


# ── 인터셉트 규칙 ──
# 매치하면 위험도·자율 모드와 무관하게 ALLOW_WITH_APPROVAL 로 강제한다(policy.decide, CLAUDE.md §8) — 이 메뉴는
# 승인을 '추가'만 할 수 있고 거부를 끄거나 승인을 건너뛰게 만들 수 없다(그런 필드 자체가 없음). 공유 파일
# (intercept_rules.json)의 규칙 이름은 웹에서 덮어쓰지 못한다 — 같은 이름으로 패턴을 좁혀 보호를 약화시키는
# 경로를 막기 위함(바꾸려면 파일을 직접 수정). 결과는 이 PC 전용 intercept_rules.local.json 에 저장된다.
class InterceptRuleBody(BaseModel):
    tool_pattern: str
    arg_pattern: str | None = None
    enabled: bool = True
    confirm: bool = False


class TestMatchBody(BaseModel):
    tool: str
    args: dict = {}


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
