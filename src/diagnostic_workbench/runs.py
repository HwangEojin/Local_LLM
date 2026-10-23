"""실행 생성: 체크리스트 버전을 DB 로 가져오고, 범위 내 대상을 자산으로 등록하고, 실행별 체크리스트 행을 만든다."""
from __future__ import annotations

from dataclasses import dataclass

from psycopg.types.json import Jsonb

from audit import config as audit_config
from audit import overlays as overlays_mod
from audit.adapters import build_registry
from audit.checklist_engine import Checklists, platform_match
from audit.models import ConfigError, Target
from audit.policy import Policy

from . import db, tools


def registry(cfg) -> dict:
    """실행 가능한 도구 전체: 기존 audit 도구(fs, MCP) + 워크벤치 로컬 도구.

    stop_file 을 넘겨 MCP 호출도 Kill Switch 로 진행 중에 취소할 수 있게 한다(CLAUDE.md §8 Kill Switch 동작).
    """
    return {**build_registry(cfg.settings, audit_config.load_mcp_servers(cfg.home), stop_file=cfg.stop_file),
            **tools.make_tools(cfg)}


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


def create_run(conn, cfg, target_ids: list[str], operator: str, *, checklist_id: str | None = None,
               version: str | None = None, check_ids: list[str] | None = None, use_llm: bool = False,
               budget: dict | None = None, allowed_tools: list[str] | None = None, autonomy: str = "approval",
               goal_text: str | None = None, llm_provider: str = "ollama") -> dict:
    """실행 생성. checklist_id 가 없으면 체크리스트 항목 없이 자율 탐색만 하는 실행이 된다 — 체크리스트는
    필수 조건이 아니라 선택적으로 곁들이는 기능이다(CLAUDE.md §3.2/§13).
    """
    if autonomy not in AUTONOMY_LEVELS:
        raise ConfigError(f"autonomy 는 {AUTONOMY_LEVELS} 중 하나여야 함: {autonomy}")
    if llm_provider not in LLM_PROVIDERS:
        raise ConfigError(f"llm_provider 는 {LLM_PROVIDERS} 중 하나여야 함: {llm_provider}")
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

    run = conn.execute(
        "INSERT INTO runs (engagement_id, operator, checklist_version_id, scope, budget, use_llm, allowed_tools, "
        "autonomy, llm_provider) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (scope["engagement_id"], operator, cv["id"] if cv else None, Jsonb(scope),
         Jsonb({**cfg.budget, **(budget or {})}), use_llm, allowed_tools, autonomy, llm_provider)).fetchone()
    default_goal = f"체크리스트 {checklist_id}@{version} 진단" if checklist_id else f"자율 진단: {', '.join(target_ids)}"
    goal = conn.execute(
        "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'goal','operator',%s) RETURNING id",
        (run["id"], Jsonb({"text": goal_text or default_goal, "targets": target_ids}))).fetchone()
    tasks = []
    for tid, t in targets:
        asset = conn.execute(
            "INSERT INTO assets (kind, value, platform, source, verification, first_seen_run_id) "
            "VALUES (%s,%s,%s,'scope','SCOPE_DECLARED',%s) "
            "ON CONFLICT (kind, value) DO UPDATE SET platform = COALESCE(EXCLUDED.platform, assets.platform) "
            "RETURNING id", (t.type, t.value, t.platform, run["id"])).fetchone()
        conn.execute("INSERT INTO run_assets (run_id, asset_id, target_id) VALUES (%s,%s,%s)",
                     (run["id"], asset["id"], tid))
        conn.execute("INSERT INTO exploration_anchors (node_id, asset_id) VALUES (%s,%s)", (goal["id"], asset["id"]))
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
        "allowed_tools": allowed_tools, "autonomy": autonomy, "llm_provider": llm_provider})
    db.notify_change(conn, run["id"], "run_created")
    conn.commit()
    return run


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
