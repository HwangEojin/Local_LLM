"""자동 점검 (A) 오버레이: 로딩 검증과 원문 비파괴 병합이 기존 verdict 엔진과 맞물려 동작하는지 확인.

DB/MCP 없이 audit.overlays + audit.verdict 만으로 끝까지 판정이 나오는지 보는 최소 확인이다.
"""
import json
import tempfile
import unittest
from pathlib import Path

from audit import overlays
from audit.models import ConfigError, Verdict
from audit.verdict import decide, evaluate_rule

ITEM = {
    "check_id": "WEB-SER-038", "title": "불필요한 웹 메서드 차단", "item_status": "incomplete",
    "source_reference": "", "target_types": ["url"], "applicability": "", "procedure": "",
    "pass_criteria": "", "fail_criteria": "",  # 원문 기준 없음(공식 체크리스트의 전형적인 상태)
    "evidence_requirements": [], "verification_method": {"type": "manual"}, "remediation": "메서드 비활성화",
    "severity": "medium", "rule_version": "orig-1",
}

OVERLAY = {
    "checklist_id": "fsec-2026-1-web-mobile-hts", "check_id": "WEB-SER-038", "platforms": ["WEB"],
    "basis": "OWASP Testing Guide — HTTP 메서드 노출", "rule_version": "2026-10-1",
    "evidence_requirements": [{"id": "options_response", "tool": "mcp:burp/send_http1_request", "args": {}}],
    "rule": {"kind": "regex", "evidence": "options_response",
             "pattern": "(?i)Allow:.*(PUT|DELETE|TRACE)", "fail_if": "match"},
}


class LoadOverlays(unittest.TestCase):
    def test_missing_file_means_no_overlays(self):
        self.assertEqual(overlays.load_overlays(Path("/no/such/file.json")), {})

    def test_loads_and_indexes_by_checklist_and_check_id(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rule_overlays.json"
            p.write_text(json.dumps({"overlays": [OVERLAY]}), encoding="utf-8")
            loaded = overlays.load_overlays(p)
            self.assertEqual(list(loaded), [("fsec-2026-1-web-mobile-hts", "WEB-SER-038")])

    def test_rejects_rule_referencing_unknown_evidence(self):
        bad = {**OVERLAY, "rule": {**OVERLAY["rule"], "evidence": "nope"}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rule_overlays.json"
            p.write_text(json.dumps({"overlays": [bad]}), encoding="utf-8")
            with self.assertRaises(ConfigError):
                overlays.load_overlays(p)

    def test_rejects_invalid_regex(self):
        bad = {**OVERLAY, "rule": {**OVERLAY["rule"], "pattern": "("}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rule_overlays.json"
            p.write_text(json.dumps({"overlays": [bad]}), encoding="utf-8")
            with self.assertRaises(ConfigError):
                overlays.load_overlays(p)


class ApplyAndJudge(unittest.TestCase):
    def test_original_pass_fail_criteria_untouched(self):
        merged = overlays.apply(ITEM, OVERLAY)
        self.assertEqual(merged["pass_criteria"], "")
        self.assertEqual(merged["fail_criteria"], "")
        self.assertEqual(merged["rule_version"], "orig-1")  # 원문 rule_version 도 그대로
        self.assertEqual(merged["overlay"]["basis"], OVERLAY["basis"])
        self.assertIs(ITEM["verification_method"]["type"], "manual")  # 원본 dict 는 바뀌지 않음

    def test_overlay_makes_item_executable_despite_incomplete_status(self):
        merged = overlays.apply(ITEM, OVERLAY)
        self.assertEqual(merged["item_status"], "complete")
        self.assertEqual(merged["verification_method"]["type"], "rule")

    def test_fail_when_dangerous_method_present(self):
        merged = overlays.apply(ITEM, OVERLAY)
        ev = {"options_response": {"evidence_id": "e1",
                                    "data": b"HTTP/1.1 200 OK\r\nAllow: GET, PUT, DELETE\r\n", "truncated": False}}
        rule_outcome = evaluate_rule(merged["verification_method"]["rule"], ev)
        verdict, _, _ = decide(merged, True, True, rule_outcome, None)
        self.assertEqual(verdict, Verdict.FAIL)

    def test_pass_when_only_safe_methods(self):
        merged = overlays.apply(ITEM, OVERLAY)
        ev = {"options_response": {"evidence_id": "e1",
                                    "data": b"HTTP/1.1 200 OK\r\nAllow: GET, HEAD, POST\r\n", "truncated": False}}
        rule_outcome = evaluate_rule(merged["verification_method"]["rule"], ev)
        verdict, _, _ = decide(merged, True, True, rule_outcome, None)
        self.assertEqual(verdict, Verdict.PASS)


if __name__ == "__main__":
    unittest.main()
