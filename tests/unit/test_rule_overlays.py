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


# nmap.ssl_scan 으로 실제 nmap 7.98 + ssl-enum-ciphers/ssl-heartbleed/ssl-ccs-injection 을 돌려 받은 실제 출력
# (강한 cipher 만 제공하는 로컬 TLS 테스트 서버, tests/unit/test_tls_inspect.py 참고).
REAL_SAFE_SSL_SCAN = """PORT      STATE SERVICE
16864/tcp open  unknown
| ssl-enum-ciphers:
|   TLSv1.2:
|     ciphers:
|       TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384 (ecdh_x25519) - A
|     cipher preference: server
|_  least strength: A
"""

# 취약한 서버를 실제로 만들 수 없어, nmap 문서의 출력 형식을 그대로 따른 구성값(실측 아님 — 패턴만 확인).
WEAK_CIPHER_SSL_SCAN = REAL_SAFE_SSL_SCAN.replace("least strength: A", "least strength: C")
VULNERABLE_SSL_SCAN = REAL_SAFE_SSL_SCAN + "\n|_ssl-heartbleed: \n|   VULNERABLE:\n|_    State: VULNERABLE\n"

_WEB_SER_035 = {**OVERLAY, "check_id": "WEB-SER-035",
               "evidence_requirements": [{"id": "cipher_scan", "tool": "nmap.ssl_scan", "args": {}}],
               "rule": {"kind": "regex", "evidence": "cipher_scan", "pattern": "least strength: [CDF]",
                        "fail_if": "match"}}
_WEB_SER_036 = {**OVERLAY, "check_id": "WEB-SER-036",
               "evidence_requirements": [{"id": "vuln_scan", "tool": "nmap.ssl_scan", "args": {}}],
               "rule": {"kind": "regex", "evidence": "vuln_scan", "pattern": "State: VULNERABLE",
                        "fail_if": "match"}}


class NmapSslScanOverlays(unittest.TestCase):
    def _judge(self, overlay, text):
        merged = overlays.apply(ITEM, overlay)
        req_id = overlay["evidence_requirements"][0]["id"]
        ev = {req_id: {"evidence_id": "e1", "data": text.encode(), "truncated": False}}
        rule_outcome = evaluate_rule(merged["verification_method"]["rule"], ev)
        verdict, _, _ = decide(merged, True, True, rule_outcome, None)
        return verdict

    def test_strong_ciphers_only_pass(self):
        self.assertEqual(self._judge(_WEB_SER_035, REAL_SAFE_SSL_SCAN), Verdict.PASS)

    def test_weak_cipher_grade_fails(self):
        self.assertEqual(self._judge(_WEB_SER_035, WEAK_CIPHER_SSL_SCAN), Verdict.FAIL)

    def test_no_known_vuln_passes(self):
        self.assertEqual(self._judge(_WEB_SER_036, REAL_SAFE_SSL_SCAN), Verdict.PASS)

    def test_heartbleed_vulnerable_fails(self):
        self.assertEqual(self._judge(_WEB_SER_036, VULNERABLE_SSL_SCAN), Verdict.FAIL)


# testssl.sh 소스(run_renego())에서 확인한 실제 출력 문구 그대로(두 FAIL 분기 모두 "VULNERABLE (NOT ok)").
SAFE_RENEGO_SCAN = (" Secure Renegotiation (RFC 5746)           supported (OK)\n"
                   " Secure Client-Initiated Renegotiation     not vulnerable (OK)\n")
NO_SECURE_RENEGO_SCAN = (" Secure Renegotiation (RFC 5746)           Not supported / VULNERABLE (NOT ok)\n"
                        " Secure Client-Initiated Renegotiation     not vulnerable (OK)\n")
_WEB_SER_037 = {**OVERLAY, "check_id": "WEB-SER-037",
               "evidence_requirements": [{"id": "renegotiation_scan", "tool": "tls.renegotiation", "args": {}}],
               "rule": {"kind": "regex", "evidence": "renegotiation_scan", "pattern": "VULNERABLE \\(NOT ok\\)",
                        "fail_if": "match"}}


class TestsslRenegotiationOverlay(unittest.TestCase):
    def _judge(self, text):
        merged = overlays.apply(ITEM, _WEB_SER_037)
        ev = {"renegotiation_scan": {"evidence_id": "e1", "data": text.encode(), "truncated": False}}
        rule_outcome = evaluate_rule(merged["verification_method"]["rule"], ev)
        verdict, _, _ = decide(merged, True, True, rule_outcome, None)
        return verdict

    def test_secure_renegotiation_passes(self):
        self.assertEqual(self._judge(SAFE_RENEGO_SCAN), Verdict.PASS)

    def test_missing_secure_renegotiation_fails(self):
        self.assertEqual(self._judge(NO_SECURE_RENEGO_SCAN), Verdict.FAIL)


if __name__ == "__main__":
    unittest.main()
