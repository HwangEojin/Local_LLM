"""이미 만들어진 결과(주로 수동확인 항목)에 등록된 도구의 결과를 증거로 추가한다.

실행 중 자동 수집(orchestrator)과 같은 정책 경로를 지난다: 허용 목록, 인자 스키마, 범위 검사, 승인, 한도.
도구 결과는 신뢰할 수 없는 데이터로 저장만 하며, 이 증거가 판정을 바꾸지는 않는다.
"""
from __future__ import annotations

from .models import AuditError, PolicyDenied, ToolError


def add_tool_evidence(storage, evidence_store, policy, registry, settings, approver, run_id, check_id, target_id,
                      tool: str, args: dict) -> dict:
    if storage.get_run(run_id) is None:
        raise AuditError(f"알 수 없는 실행 {run_id}")
    if storage.get_result(run_id, check_id, target_id) is None:
        raise AuditError(f"실행 {run_id} 에 {check_id}/{target_id} 결과가 없음")
    target = policy.target(target_id)  # 범위 밖 대상이면 PolicyDenied
    if len(storage.evidence(run_id)) >= settings.limits["max_tool_calls_per_run"]:
        raise PolicyDenied("max_tool_calls_per_run 한도에 도달함")
    spec = registry.get(tool)
    clean = policy.authorize(spec, tool, args, target, approver)
    storage.audit(run_id, "tool_call", {"check_id": check_id, "tool": tool, "args": clean, "manual": True})
    try:
        out = spec.executor(clean)
        if spec.post:
            out = spec.post(out, target)
    except ToolError as e:
        eid = evidence_store.record(run_id, check_id, target, f"manual:{tool}", tool, clean, error=str(e),
                                    tool_version=spec.version)
        storage.add_result_evidence(run_id, check_id, target_id, eid)  # 실패도 이력으로 남긴다
        storage.audit(run_id, "tool_failed", {"check_id": check_id, "tool": tool, "error": str(e)})
        raise
    eid = evidence_store.record(run_id, check_id, target, f"manual:{tool}", tool, clean, output=out)
    storage.add_result_evidence(run_id, check_id, target_id, eid)
    storage.audit(run_id, "evidence_added", {"check_id": check_id, "target_id": target_id, "tool": tool,
                                              "evidence_id": eid, "scope_filter": out.meta.get("scope_filter")})
    return {"evidence_id": eid, "scope_filter": out.meta.get("scope_filter")}
