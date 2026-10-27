"""실행 생성: 체크리스트 버전을 DB 로 가져오고, 범위 내 대상을 자산으로 등록하고, 실행별 체크리스트 행을 만든다."""
from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

from psycopg.types.json import Jsonb

from audit import config as audit_config
from audit import overlays as overlays_mod
from audit import schemas
from audit.adapters import build_registry
from audit.checklist_engine import Checklists, platform_match
from audit.local_model import LLMError
from audit.models import ConfigError, Target
from audit.policy import Policy

from . import db, explore, prompts, tools

_GOAL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["subgoals"],
    "properties": {"subgoals": {"type": "array", "minItems": 1, "maxItems": 5, "items": {
        "type": "object", "additionalProperties": False, "required": ["text", "target_id"],
        "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 300},
                       "target_id": {"type": "string", "maxLength": 300}}}}},
}
_GOAL_DECOMPOSE_SYSTEM = (
    "Split the operator's goal for an authorized security audit into independent sub-goals, one per target it "
    "actually concerns. If the goal is already single-purpose and concerns only one target, return exactly one "
    "sub-goal unchanged. Only use target_ids listed in TARGETS. Reply with JSON only. Write each sub-goal's text "
    "in Korean."
)


def _decompose_goal(conn, cfg, run: dict, goal_text: str, targets: list[tuple[str, Target]]) -> list[tuple[str, list[str]]]:
    """목표를 대상별 하위 목표로 선택적으로 나눈다(멀티에이전트 2단계 — artex 의 DecomposeGoals 참고).

    실행 권한에는 영향이 없다 — 하위 목표도 explore.propose 의 제안 → policy.decide 검증을 그대로 거치는 입력일
    뿐이다. LLM 이 없거나 실패/엉뚱한 응답이면 원래 목표 하나로 조용히 폴백한다(CLAUDE.md §7).
    """
    fallback = [(goal_text, [tid for tid, _ in targets])]
    try:
        llm = explore.make_llm(SimpleNamespace(run=run, cfg=cfg, conn=conn), "goals")
    except LLMError:
        return fallback
    text = ("GOAL: " + goal_text + "\nTARGETS: " +
            json.dumps({tid: f"{t.type}:{t.value}" for tid, t in targets}, ensure_ascii=False))
    try:
        out = llm.structured_chat([{"role": "system", "content": prompts.get(conn, "goals_system", _GOAL_DECOMPOSE_SYSTEM, "goals", run["id"])},
                                   {"role": "user", "content": text}], _GOAL_SCHEMA, "decompose_goal")
    except LLMError:
        return fallback
    known = {tid for tid, _ in targets}
    subgoals = [(s["text"], [s["target_id"]]) for s in out["subgoals"] if s["target_id"] in known]
    return subgoals or fallback


RISK_ORDER = {"read": 0, "write": 1, "dangerous": 2}


def tool_overrides(cfg) -> dict:
    """이 PC 전용 도구 오버라이드(config/tools.local.json, 웹 '도구' 화면이 씀). 없으면 변경 없음.
    파일이 깨졌으면 조용히 무시하지 않고 ConfigError — 끈 도구가 몰래 다시 켜지면 안 된다."""
    p = cfg.home / "config" / "tools.local.json"
    doc = audit_config.load_json(p, schemas.TOOL_OVERRIDES) if p.exists() else {}
    return {"disabled": list(doc.get("disabled", [])), "risk": dict(doc.get("risk", {}))}


def registry(cfg, apply_overrides: bool = True) -> dict:
    """실행 가능한 도구 전체: 기존 audit 도구(fs, MCP) + 워크벤치 로컬 도구, 그 위에 웹에서 건 오버라이드
    (도구 끄기·위험도 변경)를 적용한다. 모든 실행 경로(정책 판정·실행 생성·환경 조회)가 이 함수를 거치므로
    꺼진 도구는 어디서도 '등록되지 않은 도구'로 거부된다.

    stop_file 을 넘겨 MCP 호출도 Kill Switch 로 진행 중에 취소할 수 있게 한다(CLAUDE.md §8 Kill Switch 동작).
    apply_overrides=False 는 웹 '도구' 화면이 원래 상태(기본 위험도, 꺼진 도구 포함)를 보여 줄 때만 쓴다.
    """
    reg = {**build_registry(cfg.settings, audit_config.load_mcp_servers(cfg.home), stop_file=cfg.stop_file),
           **tools.make_tools(cfg)}
    if apply_overrides:
        ov = tool_overrides(cfg)
        for name in ov["disabled"]:
            reg.pop(name, None)
        for name, risk in ov["risk"].items():
            if name in reg:
                reg[name] = dataclasses.replace(reg[name], risk=risk)
    return reg


def automation_level(item: dict) -> str:
    """원본 항목에 있는 정보만으로 분류한다. 기준이 미완성(incomplete)이면 자동화하지 않는다."""
    method = item["verification_method"]["type"]
    if item["item_status"] != "complete" or method == "manual":
        return "MANUAL_REVIEW"
    return "AUTOMATABLE" if method == "rule" else "PARTIALLY_AUTOMATABLE"


def import_checklist(conn, checklists: Checklists, checklist_id: str, version: str,
                     overlays: dict[tuple[str, str], dict] | None = None) -> dict:
    """활성 상태이고 해시가 고정된 체크리스트 버전을 DB 에 넣는다(이미 있으면 그대로 사용).

    overlays 에 (checklist_id, check_id) 가 있으면 그 항목은 원문 pass_criteria/fail_criteria 를 그대로 두고
    verification_method/evidence_requirements 만 오버레이 것으로 바꿔 저장한다(CLAUDE.md §7A).
    """
    data, sha = checklists.load(checklist_id, version)
    row = conn.execute("SELECT * FROM checklist_versions WHERE checklist_id=%s AND version=%s AND sha256=%s",
                       (checklist_id, version, sha)).fetchone()
    if row:
        return row
    row = conn.execute(
        "INSERT INTO checklist_versions (checklist_id, version, guide_name, guide_version, official, notice, "
        "sha256, sources) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (checklist_id, version, data["guide_name"], data["guide_version"], data["official"], data["notice"],
         sha, Jsonb(data.get("sources", [])))).fetchone()
    overlays = overlays or {}
    rows = []
    for it in data["items"]:
        ov = overlays.get((checklist_id, it["check_id"]))
        body = overlays_mod.apply(it, ov) if ov else it
        level = overlays_mod.automation_level(ov) if ov else automation_level(it)
        rows.append((row["id"], it["check_id"], it["title"], it["severity"], level, Jsonb(body)))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO checklist_items (checklist_version_id, check_id, title, severity, automation_level, body) "
            "VALUES (%s,%s,%s,%s,%s,%s)", rows)
    db.audit(conn, None, "checklist_imported_to_db", {"checklist_id": checklist_id, "version": version,
                                                      "sha256": sha, "items": len(data["items"]),
                                                      "overlays_applied": sum(1 for it in data["items"]
                                                                              if (checklist_id, it["check_id"]) in overlays)})
    return row


def reapply_overlays(conn, checklists: Checklists, checklist_id: str, version: str,
                     overlays: dict[tuple[str, str], dict]) -> list[str]:
    """이미 DB 에 들어간 체크리스트 버전의 checklist_items 를 현재 오버레이 기준으로 다시 계산한다.

    원본 파일·sha256·checklist_versions 행은 바뀌지 않는다. import_checklist 가 (checklist_id, version, sha256)
    가 이미 있으면 그대로 반환해 소급 적용하지 않는 문제(README §7)를 메우는 명시적 재적용 경로다. 오버레이가
    없어진 항목은 원문 기준으로 되돌린다. 바뀐 check_id 목록을 반환하고 감사 이벤트로 남긴다.
    """
    data, sha = checklists.load(checklist_id, version)
    row = conn.execute("SELECT id FROM checklist_versions WHERE checklist_id=%s AND version=%s AND sha256=%s",
                       (checklist_id, version, sha)).fetchone()
    if not row:
        raise ConfigError(f"{checklist_id}@{version} 이(가) 아직 DB 로 import 되지 않음 (run create 로 먼저 가져오기)")
    changed = []
    for it in data["items"]:
        ov = overlays.get((checklist_id, it["check_id"]))
        body = overlays_mod.apply(it, ov) if ov else it
        level = overlays_mod.automation_level(ov) if ov else automation_level(it)
        cur = conn.execute("SELECT automation_level, body FROM checklist_items "
                           "WHERE checklist_version_id=%s AND check_id=%s",
                           (row["id"], it["check_id"])).fetchone()
        if cur and (cur["automation_level"] != level or cur["body"] != body):
            conn.execute("UPDATE checklist_items SET automation_level=%s, body=%s "
                        "WHERE checklist_version_id=%s AND check_id=%s",
                        (level, Jsonb(body), row["id"], it["check_id"]))
            changed.append(it["check_id"])
    db.audit(conn, None, "checklist_overlays_reapplied",
             {"checklist_id": checklist_id, "version": version, "changed_check_ids": changed})
    return changed


# 항상 승인 | 전부 자동(범위 내). 2026-10-19 결정으로 기존 3단계(partial)를 없애고 이분법으로 단순화했다
# (CLAUDE.md 가 3단계를 요구한 적은 없어 규칙 예외는 아님). 기존 'partial' 실행은 더 안전한 쪽인
# 'approval'로 백필됨(migrations/0007).
AUTONOMY_LEVELS = ("approval", "full")

# ollama: 기본값(로컬). claude_cli: 임시 테스트용 — 이 PC 에 로그인된 claude CLI(Claude Code 구독,
# API 과금 아님) 호출. scope/autonomy 와 같은 방식으로 생성 시점에 고정하고 실행 중 바꾸지 않는다.
LLM_PROVIDERS = ("ollama", "claude_cli")


def target_abbr(target_id: str, code: str | None = None) -> str:
    """진단 ID 의 대상 약어. scope 의 대상에 code 가 있으면 그것을, 없으면 target_id 의 첫 단어 앞 4글자를 쓴다.
    (migrations/0009 의 백필과 같은 규칙)"""
    if code:
        return code
    words = re.sub(r"[^A-Za-z0-9]+", " ", target_id).split()
    return words[0][:4].upper() if words else "RUN"


def next_run_code(conn, abbr: str) -> str:
    """<약어>-<YYYYMMDD>-<일련번호>. 같은 약어·날짜 안에서 1부터 늘어난다.
    advisory lock 은 호출한 트랜잭션이 끝날 때(create_run 의 commit)까지 유지돼 동시 생성 시 번호가 겹치지 않는다."""
    day = datetime.now().strftime("%Y%m%d")
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('run_code'))")
    last = conn.execute("SELECT max(substring(run_code from '-(\\d+)$')::int) AS n FROM runs WHERE run_code LIKE %s",
                        (f"{abbr}-{day}-%",)).fetchone()["n"]
    return f"{abbr}-{day}-{(last or 0) + 1:02d}"


def _check_agent_models(cfg, agent_models: dict[str, str], use_llm: bool) -> dict[str, str]:
    """역할별 모델 지정을 검증한다. 값은 llm_profiles.json 의 프로파일 이름 또는 'ollama:<설치된 모델>'.
    지정은 운영자가 실행 생성 시에만 하고(LLM 이 바꾸지 못함) 이후 고정된다. Ollama 에 연결할 수 없으면
    설치 여부는 검증하지 못하므로 통과시키고, 실제로 쓸 때 check_ready 가 막는다."""
    if not agent_models:
        return {}
    if not use_llm:
        raise ConfigError("에이전트별 모델 지정은 --llm 과 함께만 쓸 수 있음")
    bad = sorted(set(agent_models) - set(explore.AGENT_ROLES))
    if bad:
        raise ConfigError(f"알 수 없는 에이전트 역할: {bad} (가능: {list(explore.AGENT_ROLES)})")
    profiles = audit_config.load_llm_profiles(cfg.home)
    installed = None
    for role, spec in agent_models.items():
        if spec.startswith("ollama:"):
            installed = explore.installed_ollama_models(cfg) if installed is None else installed
            name = spec[len("ollama:"):]
            if not name:
                raise ConfigError(f"{role}: 'ollama:' 뒤에 모델명이 필요함")
            if installed and name not in installed and f"{name}:latest" not in installed:
                raise ConfigError(f"{role}: 모델 '{name}' 이(가) 설치되어 있지 않음 (설치된 모델: {installed})")
        elif spec not in profiles:
            raise ConfigError(f"{role}: '{spec}' 은(는) llm_profiles.json 프로파일도 'ollama:<모델>' 도 아님 "
                              f"(등록된 프로파일: {sorted(profiles) or '없음'})")
    return dict(agent_models)


def create_run(conn, cfg, target_ids: list[str], operator: str, *, checklist_id: str | None = None,
               version: str | None = None, check_ids: list[str] | None = None, use_llm: bool = False,
               budget: dict | None = None, allowed_tools: list[str] | None = None, autonomy: str = "approval",
               goal_text: str | None = None, llm_provider: str = "ollama", llm_profile: str | None = None,
               agent_models: dict[str, str] | None = None) -> dict:
    """실행 생성. checklist_id 가 없으면 체크리스트 항목 없이 자율 탐색만 하는 실행이 된다 — 체크리스트는
    필수 조건이 아니라 선택적으로 곁들이는 기능이다(CLAUDE.md §3.2/§13).

    llm_profile 을 주면 config/llm_profiles.json 에서 그 이름을 찾아 llm_provider 를 그 프로파일의 kind 로
    맞춘다(둘 다 주면 llm_profile 이 우선). 프로파일이 없으면 llm_provider 하나만으로 기존처럼 동작한다.
    """
    if autonomy not in AUTONOMY_LEVELS:
        raise ConfigError(f"autonomy 는 {AUTONOMY_LEVELS} 중 하나여야 함: {autonomy}")
    if llm_profile:
        profiles = audit_config.load_llm_profiles(cfg.home)
        profile = profiles.get(llm_profile)
        if profile is None:
            raise ConfigError(f"llm_profile '{llm_profile}' 을(를) config/llm_profiles.json 에서 찾을 수 없음 "
                              f"(등록된 프로파일: {sorted(profiles) or '없음'})")
        llm_provider = profile["kind"]
    if llm_provider not in LLM_PROVIDERS:
        raise ConfigError(f"llm_provider 는 {LLM_PROVIDERS} 중 하나여야 함: {llm_provider}")
    agent_models = _check_agent_models(cfg, agent_models or {}, use_llm)
    if not checklist_id and not use_llm:
        raise ConfigError("체크리스트 없이 실행하려면 --llm 이 필요함(자율 탐색은 LLM 제안에 의존함)")
    scope = audit_config.load_scope(cfg.home)
    policy = Policy(scope, cfg.home)
    targets = [(tid, policy.target(tid)) for tid in dict.fromkeys(target_ids)]  # 범위 밖이면 PolicyDenied
    if allowed_tools is not None:
        known = registry(cfg)
        unknown = sorted(set(allowed_tools) - set(known))
        if unknown:
            raise ConfigError(f"등록되지 않은 도구: {unknown}. 등록된 도구: {sorted(known)}")

    cv, items = None, []
    if checklist_id:
        overlays = overlays_mod.load_overlays(cfg.home / "config" / "rule_overlays.json")
        cv = import_checklist(conn, Checklists(cfg.settings.path("checklist_dir")), checklist_id, version, overlays)
        items = conn.execute("SELECT * FROM checklist_items WHERE checklist_version_id=%s ORDER BY check_id",
                             (cv["id"],)).fetchall()
        if check_ids:
            unknown = sorted(set(check_ids) - {i["check_id"] for i in items})
            if unknown:
                raise ConfigError(f"알 수 없는 check_id: {unknown}")
            items = [i for i in items if i["check_id"] in set(check_ids)]
        platforms = sorted({p for i in items for p in i["body"].get("platforms") or []})
        for tid, t in targets:
            if platforms and t.platform not in platforms:
                raise ConfigError(f"대상 '{tid}' 의 platform '{t.platform}' 이(가) 체크리스트에 없음 (scope.json). "
                                  f"사용 가능: {', '.join(platforms)}")

    first = targets[0][0]
    code = next((t.get("code") for t in scope["targets"] if t["target_id"] == first), None)
    run_code = next_run_code(conn, target_abbr(first, code))
    run = conn.execute(
        "INSERT INTO runs (engagement_id, operator, checklist_version_id, scope, budget, use_llm, allowed_tools, "
        "autonomy, llm_provider, run_code, llm_profile_name, llm_agent_models) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (scope["engagement_id"], operator, cv["id"] if cv else None, Jsonb(scope),
         Jsonb({**cfg.budget, **(budget or {})}), use_llm, allowed_tools, autonomy, llm_provider, run_code,
         llm_profile, Jsonb(agent_models))).fetchone()
    default_goal = f"체크리스트 {checklist_id}@{version} 진단" if checklist_id else f"자율 진단: {', '.join(target_ids)}"
    # 운영자가 목표를 직접 썼고 LLM 을 쓰는 실행이면 대상별 하위 목표로 나눠볼 수 있다(2단계, §13의 default_goal
    # 은 나눌 의미가 없어 대상으로 그대로 둠). 실패/미사용 시 기존처럼 목표 하나로 폴백한다.
    subgoals = _decompose_goal(conn, cfg, run, goal_text, targets) if goal_text and use_llm \
        else [(goal_text or default_goal, target_ids)]
    goal_ids_by_target: dict[str, list] = {tid: [] for tid, _ in targets}
    for text, tids in subgoals:
        gid = conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'goal','operator',%s) "
            "RETURNING id", (run["id"], Jsonb({"text": text, "targets": tids}))).fetchone()["id"]
        for tid in tids:
            goal_ids_by_target.setdefault(tid, []).append(gid)
    tasks = []
    for tid, t in targets:
        asset = conn.execute(
            "INSERT INTO assets (kind, value, platform, company, source, verification, first_seen_run_id) "
            "VALUES (%s,%s,%s,%s,'scope','SCOPE_DECLARED',%s) "
            "ON CONFLICT (company, kind, value) DO UPDATE SET platform = COALESCE(EXCLUDED.platform, assets.platform) "
            "RETURNING id", (t.type, t.value, t.platform, t.company, run["id"])).fetchone()
        conn.execute("INSERT INTO run_assets (run_id, asset_id, target_id) VALUES (%s,%s,%s)",
                     (run["id"], asset["id"], tid))
        for gid in goal_ids_by_target.get(tid, []):
            conn.execute("INSERT INTO exploration_anchors (node_id, asset_id) VALUES (%s,%s)", (gid, asset["id"]))
        for it in items:
            if platform_match(it["body"], t):  # 다른 platform 용 항목은 이 대상의 진단 항목이 아니다
                conn.execute("INSERT INTO run_checklist_items (run_id, checklist_item_id, asset_id) "
                             "VALUES (%s,%s,%s)", (run["id"], it["id"], asset["id"]))
                tasks.append([it["check_id"], tid])
    # details 의 plan/scope/tasks 형식은 기존 audit 보고서 모듈이 읽는 형식과 같다
    db.audit(conn, run["id"], "run_created", {
        "plan": {"plan_id": str(run["id"]), "checklist_id": checklist_id, "checklist_version": version,
                 "targets": [tid for tid, _ in targets], "use_llm": use_llm},
        "scope": scope, "tasks": tasks, "use_llm": use_llm, "operator": operator, "budget": run["budget"],
        "allowed_tools": allowed_tools, "autonomy": autonomy, "llm_provider": llm_provider,
        "llm_profile": llm_profile, "agent_models": agent_models})
    db.notify_change(conn, run["id"], "run_created")
    conn.commit()
    return run


def soft_delete_run(conn, run: dict, actor: str, reason: str) -> None:
    """진단을 '삭제됨'으로 표시한다. 행·증적·감사 로그는 그대로 보존된다(0011 참고). 복원 가능.

    '실행 중인 루프가 있는지'는 이 프로세스의 상태라 호출하는 쪽(웹 라우터)이 먼저 확인한다.
    """
    if run.get("deleted_at"):
        raise ConfigError(f"이미 삭제된 진단: {run['run_code']}")
    if not reason.strip():
        raise ConfigError("삭제 사유가 필요함")
    conn.execute("UPDATE runs SET deleted_at=now(), deleted_by=%s, delete_reason=%s, updated_at=now() WHERE id=%s",
                 (actor, reason.strip(), run["id"]))
    db.audit(conn, run["id"], "run_deleted", {"run_code": run["run_code"], "status": run["status"],
                                              "actor": actor, "reason": reason.strip()})
    conn.commit()


def restore_run(conn, run: dict, actor: str, reason: str) -> None:
    if not run.get("deleted_at"):
        raise ConfigError(f"삭제되지 않은 진단: {run['run_code']}")
    conn.execute("UPDATE runs SET deleted_at=NULL, deleted_by=NULL, delete_reason=NULL, updated_at=now() "
                 "WHERE id=%s", (run["id"],))
    db.audit(conn, run["id"], "run_restored", {"run_code": run["run_code"], "actor": actor,
                                               "reason": reason.strip(), "was_deleted_by": run["deleted_by"],
                                               "was_delete_reason": run["delete_reason"]})
    conn.commit()


@dataclass
class RunContext:
    cfg: object
    conn: object
    run: dict
    policy: Policy            # 실행 생성 시점의 scope 사본으로 만든 정책
    registry: dict
    targets: dict             # asset_id -> Target (지금의 scope.json 에도 그대로 있는 대상만)
    target_ids: dict          # asset_id -> scope target_id
    llm: object = None
    last_call: float = 0.0    # 마지막 도구 호출 시각(monotonic), 호출 간격 제한용


def build_context(cfg, conn, run: dict) -> RunContext:
    policy = Policy(run["scope"], cfg.home)  # scope 가 만료됐으면 ConfigError
    current = Policy(audit_config.load_scope(cfg.home), cfg.home).targets
    targets, target_ids = {}, {}
    for r in conn.execute("SELECT asset_id, target_id FROM run_assets WHERE run_id=%s", (run["id"],)):
        target_ids[r["asset_id"]] = r["target_id"]
        t: Target | None = policy.targets.get(r["target_id"])
        if t is not None and current.get(r["target_id"]) == t:  # 실행 후 범위에서 빠졌거나 바뀐 대상은 제외
            targets[r["asset_id"]] = t
    return RunContext(cfg, conn, run, policy, registry(cfg), targets, target_ids)
