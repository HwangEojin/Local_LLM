"""정책 판정 계층(LLM 외부). 모든 도구 호출은 decide() 의 명시적 결과를 받아야 한다.

범위·인자·위험도 검사는 기존 audit.policy.Policy 를 그대로 쓰고, 여기서는 제외 대상, 전체 중지 스위치,
DB 에 기록된 승인(실행·대상·도구·정확한 인자에 묶임)을 더해 네 가지 결과로 정리한다. 판단할 수 없으면 거부한다.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from audit.models import PolicyDenied

ALLOW, ALLOW_WITH_APPROVAL = "ALLOW", "ALLOW_WITH_APPROVAL"
DENY, NEEDS_SCOPE_CONFIRMATION = "DENY", "NEEDS_SCOPE_CONFIRMATION"


@dataclass
class Decision:
    decision: str
    reason: str
    args: dict  # 정책이 정규화한 인자(ALLOW 일 때만 실행에 사용)


def args_hash(args: dict) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _excluded(value: str, exclude: list[str]) -> str | None:
    # ponytail: 문자열 접두어/호스트 일치만 본다. CIDR·와일드카드가 필요해지면 ipaddress/fnmatch 로 확장.
    v = value.replace("\\", "/").lower()
    host = (urlsplit(v).hostname or "") if "://" in v else ""
    for ex in exclude:
        e = ex.replace("\\", "/").lower().rstrip("/")
        if v == e or v.startswith(e + "/") or host == e:
            return ex
    return None


def decide(ctx, tool: str, args, asset_id) -> Decision:
    cfg, target = ctx.cfg, ctx.targets.get(asset_id)
    raw = args if isinstance(args, dict) else {}
    if cfg.stop_file.exists():
        return Decision(DENY, f"전체 중지 스위치가 켜져 있음 ({cfg.stop_file})", raw)
    if target is None:
        return Decision(NEEDS_SCOPE_CONFIRMATION, "대상이 현재 승인된 범위(scope.json)에서 확인되지 않음", raw)
    spec = ctx.registry.get(tool)
    try:
        clean = ctx.policy.authorize(spec, tool, args, target, dry_run=True)
    except PolicyDenied as e:
        return Decision(DENY, str(e), raw)
    for value in [target.value] + [clean[a] for a in spec.scope_args if a in clean]:
        hit = _excluded(value, cfg.exclude)
        if hit:
            return Decision(DENY, f"제외 대상 '{hit}' 에 해당함: {value}", clean)
    if spec.risk == "read":
        return Decision(ALLOW, "읽기 전용 도구, 범위 내", clean)
    row = ctx.conn.execute(
        "SELECT decision, approver FROM approvals WHERE run_id=%s AND asset_id=%s AND tool=%s AND args_sha256=%s "
        "AND expires_at > now() ORDER BY decided_at DESC LIMIT 1",
        (ctx.run["id"], asset_id, tool, args_hash(clean))).fetchone()
    if row is None:
        return Decision(ALLOW_WITH_APPROVAL, f"위험도 '{spec.risk}' 도구는 유효한 승인이 필요함", clean)
    if row["decision"] != "APPROVED":
        return Decision(DENY, f"승인자 {row['approver']} 이(가) 거부함", clean)
    return Decision(ALLOW, f"승인됨 (승인자 {row['approver']})", clean)
