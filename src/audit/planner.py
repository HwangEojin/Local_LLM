"""계획 검증. 계획은 체크리스트/대상/항목을 선택할 뿐 권한을 부여하지 않는다."""
from __future__ import annotations

import hashlib
import json

from . import schemas
from .checklist_engine import Checklists, is_applicable, platform_match, render_args
from .models import ConfigError, PolicyDenied
from .policy import Policy


def validate_plan(plan: dict, policy: Policy, checklists: Checklists, registry: dict) -> dict:
    errs = schemas.validate(plan, schemas.PLAN)
    if errs:
        raise ConfigError("계획이 유효하지 않음:\n  " + "\n  ".join(errs))
    checklist, sha = checklists.load(plan["checklist_id"], plan["checklist_version"])
    targets = [policy.target(t) for t in plan["targets"]]  # 범위 밖이면 PolicyDenied 발생
    by_id = {it["check_id"]: it for it in checklist["items"]}
    wanted = plan.get("check_ids") or list(by_id)
    unknown = [c for c in wanted if c not in by_id]
    if unknown:
        raise ConfigError(f"계획이 알 수 없는 check_id 를 참조함: {unknown}")

    all_platforms = sorted({p for cid in wanted for p in by_id[cid].get("platforms") or []})
    for t in targets:
        if all_platforms and t.platform is None:
            raise ConfigError(f"대상 '{t.target_id}' 에 platform 지정이 필요함 (scope.json). "
                              f"이 체크리스트의 platform: {', '.join(all_platforms)}")
        if all_platforms and t.platform not in all_platforms:
            raise ConfigError(f"대상 '{t.target_id}' 의 platform '{t.platform}' 은(는) 체크리스트에 없음. "
                              f"사용 가능: {', '.join(all_platforms)}")

    tasks, calls, denied = [], [], []
    for cid in wanted:
        item = by_id[cid]
        for t in targets:
            if not platform_match(item, t):
                continue  # 다른 platform 용 항목은 이 대상의 진단 항목이 아니다
            tasks.append((item, t))
            if not is_applicable(item, t) or item["item_status"] != "complete":
                continue
            for req in item["evidence_requirements"]:
                args = render_args(req["args"], t)
                spec = registry.get(req["tool"])
                entry = {"check_id": cid, "target_id": t.target_id, "tool": req["tool"], "args": args}
                try:
                    entry["args"] = policy.authorize(spec, req["tool"], args, t, dry_run=True)
                    entry["decision"] = "승인필요" if spec.risk != "read" else "허용"
                except PolicyDenied as e:
                    entry["decision"] = f"거부됨: {e}"
                    denied.append(entry)
                calls.append(entry)
    if denied:
        raise PolicyDenied("계획에 정책이 거부한 도구 호출이 포함됨:\n  " +
                           "\n  ".join(f"{d['check_id']}/{d['target_id']} {d['tool']}: {d['decision']}"
                                       for d in denied))
    return {"plan": plan, "plan_sha256": hashlib.sha256(
                json.dumps(plan, sort_keys=True).encode()).hexdigest(),
            "checklist": checklist, "checklist_sha256": sha, "tasks": tasks, "expected_calls": calls}
