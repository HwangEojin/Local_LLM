"""실행 관리: 순차 작업, 정책 검사를 거친 도구 호출, 증거, 판정, 재개."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from . import schemas
from .checklist_engine import is_applicable, render_args
from .evidence import EvidenceStore
from .local_model import LLMError, build_analysis_messages
from .models import AuditError, PolicyDenied, ToolError
from .policy import redact, secret_values
from .verdict import decide, evaluate_rule


class RunTimeout(AuditError):
    pass


class Orchestrator:
    def __init__(self, storage, evidence: EvidenceStore, policy, registry, settings,
                 llm=None, approver=None):
        self.storage, self.evidence, self.policy = storage, evidence, policy
        self.registry, self.settings, self.llm, self.approver = registry, settings, llm, approver

    def start(self, resolved: dict) -> str:
        plan = resolved["plan"]
        run_id = f"RUN-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
        self.storage.create_run(run_id, plan, resolved["plan_sha256"], plan["checklist_id"],
                                plan["checklist_version"], resolved["checklist_sha256"],
                                self.policy.scope["engagement_id"])
        self.storage.audit(run_id, "run_created", {"plan": plan, "use_llm": self.llm is not None,
                                                        "scope": self.policy.scope,
                                                        "tasks": [[i["check_id"], t.target_id]
                                                                  for i, t in resolved["tasks"]]})
        return self.execute(run_id, resolved)

    def resume(self, run_id: str, resolved: dict) -> str:
        run = self.storage.get_run(run_id)
        if run is None:
            raise AuditError(f"알 수 없는 실행 {run_id}")
        if run["status"] == "COMPLETED":
            raise AuditError(f"실행 {run_id} 은(는) 이미 완료됨")
        if run["checklist_sha256"] != resolved["checklist_sha256"] or run["plan_sha256"] != resolved["plan_sha256"]:
            raise AuditError("실행 시작 이후 체크리스트 또는 계획이 변경되어 재개를 거부함")
        self.storage.audit(run_id, "run_resumed", {})
        return self.execute(run_id, resolved)

    def execute(self, run_id: str, resolved: dict) -> str:
        self.run_id = run_id
        self.resolved = resolved
        self.tool_calls = sum(1 for e in self.storage.evidence(run_id) if not e["error"] or
                              not e["error"].startswith("정책 거부"))
        deadline = time.monotonic() + self.settings.limits["run_timeout_s"]
        self.storage.set_run_status(run_id, "RUNNING")
        try:
            for item, target in resolved["tasks"]:
                if self.storage.get_result(run_id, item["check_id"], target.target_id):
                    continue  # 이전(중단된) 실행에서 이미 완료됨
                if time.monotonic() > deadline:
                    raise RunTimeout("run_timeout_s 를 초과함. --resume 으로 재개하세요")
                self._process(item, target)
        except KeyboardInterrupt:
            self.storage.set_run_status(run_id, "ABORTED", "사용자가 중단함")
            self.storage.audit(run_id, "run_aborted", {"reason": "KeyboardInterrupt"})
            raise
        except Exception as e:
            self.storage.set_run_status(run_id, "FAILED", f"{type(e).__name__}: {e}")
            self.storage.audit(run_id, "run_failed", {"error": str(e)})
            raise
        self.storage.set_run_status(run_id, "COMPLETED")
        self.storage.audit(run_id, "run_completed", {"results": len(self.storage.results(run_id))})
        return run_id

    def _collect(self, item, target):
        evidence, ids, complete = {}, [], True
        for req in item["evidence_requirements"]:
            tool, args = req["tool"], render_args(req["args"], target)
            spec = self.registry.get(tool)

            def record(**kw):
                eid = self.evidence.record(self.run_id, item["check_id"], target, req["id"], tool, args,
                                           tool_version=spec.version if spec else "", **kw)
                ids.append(eid)
                return eid
            try:
                clean = self.policy.authorize(spec, tool, args, target, self.approver)
            except PolicyDenied as e:
                self.storage.audit(self.run_id, "policy_denied", {"check_id": item["check_id"],
                                                                  "tool": tool, "reason": str(e)})
                record(error=f"정책 거부: {e}")
                complete = False
                continue
            if self.tool_calls >= self.settings.limits["max_tool_calls_per_run"]:
                record(error="정책 거부: max_tool_calls_per_run 한도에 도달함")
                complete = False
                continue
            attempts = 2 if spec.risk == "read" else 1  # 상태를 변경하는 도구는 재시도하지 않는다
            for attempt in range(attempts):
                self.tool_calls += 1
                self.storage.audit(self.run_id, "tool_call", {"check_id": item["check_id"], "tool": tool,
                                                              "args": clean, "attempt": attempt + 1})
                try:
                    out = spec.executor(clean)
                    if spec.post:
                        out = spec.post(out, target)
                except ToolError as e:
                    err = str(e)
                    continue
                eid = record(output=out)
                evidence[req["id"]] = {"evidence_id": eid, "data": out.data, "truncated": out.truncated}
                break
            else:
                self.storage.audit(self.run_id, "tool_failed", {"check_id": item["check_id"],
                                                                "tool": tool, "error": err})
                record(error=err)
                complete = False
        return evidence, ids, complete

    def _process(self, item, target):
        applicable = is_applicable(item, target)
        evidence, ids, complete = {}, [], True
        if applicable and item["item_status"] == "complete":
            evidence, ids, complete = self._collect(item, target)
        method = item["verification_method"]
        rule_outcome = evaluate_rule(method["rule"], evidence) \
            if method["type"] == "rule" and applicable and complete else None
        llm_outcome = None
        if (self.llm and applicable and complete and item["item_status"] == "complete"
                and method["type"] in ("rule", "llm_assisted") and evidence):
            llm_outcome = self._analyze(item, target, evidence)
        verdict, mtype, reason = decide(item, applicable, complete, rule_outcome, llm_outcome)
        self.storage.save_result({
            "run_id": self.run_id, "check_id": item["check_id"], "target_id": target.target_id,
            "verdict": verdict.value, "method": mtype, "reason": reason,
            "rule_outcome": rule_outcome, "llm_outcome": llm_outcome, "evidence_ids": ids,
            "checklist_id": self.resolved["plan"]["checklist_id"],
            "checklist_version": self.resolved["plan"]["checklist_version"],
            "rule_version": item["rule_version"]})

    def _analyze(self, item, target, evidence):
        msgs = build_analysis_messages(item, target, evidence,
                                       self.settings.ollama["max_request_bytes"] // 2)
        try:
            out = self.llm.structured_chat(msgs, schemas.LLM_ANALYSIS, f"analyze:{item['check_id']}")
        except LLMError as e:
            self.storage.audit(self.run_id, "llm_error", {"check_id": item["check_id"], "error": str(e)})
            return {"verdict": "ERROR", "error": str(e)}
        known = {e["evidence_id"] for e in evidence.values()}
        secrets: set[str] = set()
        for e in evidence.values():
            secrets |= secret_values((e["data"] or b"").decode("utf-8", errors="replace"))
        out["rationale"] = redact(out["rationale"], secrets)  # 근거에 인용된 비밀 값이 DB/보고서에 남지 않게 한다
        if not out["evidence_ids"] or not set(out["evidence_ids"]) <= known:
            return {"verdict": "ERROR", "error": "LLM 이 증거 id 를 인용하지 않았거나 알 수 없는 id 를 인용함", "raw": out}
        return out
