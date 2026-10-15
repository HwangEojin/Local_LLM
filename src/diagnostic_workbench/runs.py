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
    """실행 가능한 도구 전체: 기존 audit 도구(fs, MCP) + 워크벤치 로컬 도구."""
    return {**build_registry(cfg.settings, audit_config.load_mcp_servers(cfg.home)), **tools.make_tools(cfg)}


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


AUTONOMY_LEVELS = ("approval", "partial", "full")  # 항상 승인 | write 자동, dangerous 승인 | 전부 자동(범위 내)


def create_run(conn, cfg, checklist_id: str, version: str, target_ids: list[str], operator: str,
               check_ids: list[str] | None = None, use_llm: bool = False, budget: dict | None = None,
               allowed_tools: list[str] | None = None, autonomy: str = "approval") -> dict:
    if autonomy not in AUTONOMY_LEVELS:
        raise ConfigError(f"autonomy 는 {AUTONOMY_LEVELS} 중 하나여야 함: {autonomy}")
    scope = audit_config.load_scope(cfg.home)
    policy = Policy(scope, cfg.home)
    targets = [(tid, policy.target(tid)) for tid in dict.fromkeys(target_ids)]  # 범위 밖이면 PolicyDenied
    overlays = overlays_mod.load_overlays(cfg.home / "config" / "rule_overlays.json")
    cv = import_checklist(conn, Checklists(cfg.settings.path("checklist_dir")), checklist_id, version, overlays)
    items = conn.execute("SELECT * FROM checklist_items WHERE checklist_version_id=%s ORDER BY check_id",
                         (cv["id"],)).fetchall()
    if check_ids:
        unknown = sorted(set(check_ids) - {i["check_id"] for i in items})
        if unknown:
            raise ConfigError(f"알 수 없는 check_id: {unknown}")
        items = [i for i in items if i["check_id"] in set(check_ids)]
    if allowed_tools is not None:
        known = registry(cfg)
        unknown = sorted(set(allowed_tools) - set(known))
        if unknown:
            raise ConfigError(f"등록되지 않은 도구: {unknown}. 등록된 도구: {sorted(known)}")
    platforms = sorted({p for i in items for p in i["body"].get("platforms") or []})
    for tid, t in targets:
        if platforms and t.platform not in platforms:
            raise ConfigError(f"대상 '{tid}' 의 platform '{t.platform}' 이(가) 체크리스트에 없음 (scope.json). "
                              f"사용 가능: {', '.join(platforms)}")

    run = conn.execute(
        "INSERT INTO runs (engagement_id, operator, checklist_version_id, scope, budget, use_llm, allowed_tools, "
        "autonomy) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (scope["engagement_id"], operator, cv["id"], Jsonb(scope), Jsonb({**cfg.budget, **(budget or {})}),
         use_llm, allowed_tools, autonomy)).fetchone()
    goal = conn.execute(
        "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'goal','operator',%s) RETURNING id",
        (run["id"], Jsonb({"text": f"체크리스트 {checklist_id}@{version} 진단", "targets": target_ids}))).fetchone()
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
        "allowed_tools": allowed_tools, "autonomy": autonomy})
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
