"""결정론적 규칙 평가와 최종 판정.

원칙: 증거가 없으면 PASS 불가, LLM 의견만으로 확정 불가,
규칙/LLM 불일치는 MANUAL_REVIEW, INCONCLUSIVE/MANUAL_REVIEW 는 상향 변경하지 않는다.
"""
from __future__ import annotations

import re

from .models import Verdict
from .policy import mask_secrets


def evaluate_rule(rule: dict, evidence: dict) -> dict:
    """evidence: requirement_id -> {"evidence_id", "data": bytes|None, "truncated": bool}."""
    ev = evidence.get(rule["evidence"])
    if ev is None or ev["data"] is None:
        return {"outcome": Verdict.INCONCLUSIVE.value, "evidence_ids": [ev["evidence_id"]] if ev else [],
                "detail": f"required evidence '{rule['evidence']}' not collected"}
    text = ev["data"].decode("utf-8", errors="replace")
    m = re.search(rule["pattern"], text)
    detail = {"pattern": rule["pattern"], "fail_if": rule["fail_if"], "matched": bool(m)}
    if m:
        end = text.find("\n", m.end())
        line = text[text.rfind("\n", 0, m.start()) + 1: len(text) if end == -1 else end]
        detail["match_excerpt"] = mask_secrets(line.strip())[:300]
        outcome = Verdict.FAIL if rule["fail_if"] == "match" else Verdict.PASS
    elif ev["truncated"]:
        # 잘린 증거에서 패턴이 없다는 사실은 아무것도 증명하지 못한다.
        outcome = Verdict.INCONCLUSIVE
        detail["note"] = "evidence truncated; absence of pattern cannot be confirmed"
    else:
        outcome = Verdict.PASS if rule["fail_if"] == "match" else Verdict.FAIL
    return {"outcome": outcome.value, "evidence_ids": [ev["evidence_id"]], "detail": detail}


def decide(item: dict, applicable: bool, evidence_complete: bool,
           rule_outcome: dict | None, llm_outcome: dict | None) -> tuple[Verdict, str, str]:
    """(판정, 방법, 사유)를 반환한다."""
    method = item["verification_method"]["type"]
    if not applicable:
        return Verdict.NOT_APPLICABLE, method, "대상 유형이 항목의 적용 대상(target_types)에 해당하지 않음"
    if item["item_status"] != "complete":
        return Verdict.MANUAL_REVIEW, method, "항목 기준 미완성(공식 원문 미확보) — 자동 판정 불가"
    if not evidence_complete:
        return Verdict.INCONCLUSIVE, method, "필수 증거 수집 실패 또는 누락 — 판정 불가"
    if method == "manual":
        return Verdict.MANUAL_REVIEW, method, "수동 확인 항목"
    if method == "llm_assisted":
        return Verdict.MANUAL_REVIEW, method, "LLM 분석은 참고용이며 단독으로 판정을 확정하지 않음"
    # method == "rule"
    rule_v = Verdict(rule_outcome["outcome"]) if rule_outcome else Verdict.INCONCLUSIVE
    if rule_v not in (Verdict.PASS, Verdict.FAIL):
        return Verdict.INCONCLUSIVE, method, "규칙 검사로 판정할 수 없음"
    if llm_outcome is not None:
        if llm_outcome.get("verdict") != rule_v.value:
            return (Verdict.MANUAL_REVIEW, method,
                    f"규칙 결과({rule_v.value})와 LLM 분석({llm_outcome.get('verdict', 'ERROR')}) 불일치 — 수동 확인 필요")
        return rule_v, method, "규칙 검사 결과와 LLM 분석 일치"
    return rule_v, method, "결정론적 규칙 검사 결과"
