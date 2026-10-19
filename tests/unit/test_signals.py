"""자동 점검 (B) 검증 신호: classify_payload/verify_sqli/verify_xss/verify_csrf 가 반사·오류 문구·성공
코드 하나만으로 확정하지 않고, 인과관계(보낸 페이로드가 실제로 응답에 있는지)와 문맥(인코딩 여부, 엔진
특정 오류, 거부 문구 부재)을 함께 보는지 확인.
"""
import unittest

from audit import signals

_CSRF_REQUEST = ("POST /account/email HTTP/1.1\r\n"
                 "Host: victim.example\r\n"
                 "Cookie: session=abc123\r\n"
                 "Origin: https://attacker.example\r\n"
                 "Content-Length: 20\r\n\r\n"
                 "email=new@evil.test")


class ClassifyPayload(unittest.TestCase):
    def test_no_known_signature_returns_none(self):
        self.assertIsNone(signals.classify_payload({"q": "hello world"}))

    def test_detects_xss_signature_in_nested_args(self):
        self.assertEqual(signals.classify_payload({"body": {"q": "<script>alert(1)</script>"}}), "XSS")

    def test_detects_sqli_signature(self):
        self.assertEqual(signals.classify_payload({"q": "' OR '1'='1"}), "SQLI")

    def test_detects_csrf_structure(self):
        self.assertEqual(signals.classify_payload({"request": _CSRF_REQUEST}), "CSRF")

    def test_same_origin_state_change_is_not_csrf(self):
        same_origin = _CSRF_REQUEST.replace("https://attacker.example", "https://victim.example")
        self.assertIsNone(signals.classify_payload({"request": same_origin}))

    def test_no_cookie_is_not_csrf(self):
        no_cookie = ("POST /account/email HTTP/1.1\r\nHost: victim.example\r\n"
                    "Origin: https://attacker.example\r\n\r\nemail=new@evil.test")
        self.assertIsNone(signals.classify_payload({"request": no_cookie}))

    def test_detects_ssrf_cloud_metadata_address(self):
        self.assertEqual(signals.classify_payload({"url": "http://169.254.169.254/latest/meta-data/"}), "SSRF")

    def test_detects_ssrf_file_scheme(self):
        self.assertEqual(signals.classify_payload({"url": "file:///etc/passwd"}), "SSRF")

    def test_plain_localhost_is_not_classified_as_ssrf(self):
        # 이 프로젝트 자체의 진단 대상도 localhost 일 수 있어, 그것만으로는 SSRF 시도로 보지 않는다.
        self.assertIsNone(signals.classify_payload({"url": "http://localhost:9999/health"}))

    def test_detects_ssti_arithmetic_probe(self):
        self.assertEqual(signals.classify_payload({"name": "{{7*7}}"}), "SSTI")

    def test_detects_xxe_entity_declaration(self):
        xml = '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><foo>&xxe;</foo>'
        self.assertEqual(signals.classify_payload({"body": xml}), "XXE")

    def test_detects_command_injection_signature(self):
        self.assertEqual(signals.classify_payload({"name": "test; id"}), "CMDI")


class VerifySqli(unittest.TestCase):
    def test_generic_500_is_not_enough(self):
        r = signals.verify_sqli({"q": "' OR '1'='1"}, b"HTTP/1.1 500 Internal Server Error\r\n\r\noops")
        self.assertFalse(r.matched)

    def test_engine_specific_error_matches(self):
        r = signals.verify_sqli({"q": "' OR '1'='1"},
                                b"...You have an error in your SQL syntax; check the manual for your MySQL...")
        self.assertTrue(r.matched)


class VerifyXss(unittest.TestCase):
    def test_no_reflection_does_not_match(self):
        r = signals.verify_xss({"q": "<script>alert(1)</script>"}, b"<html>nothing here</html>")
        self.assertFalse(r.matched)

    def test_encoded_reflection_does_not_match(self):
        r = signals.verify_xss({"q": "<script>alert(1)</script>"},
                               b"<html>you searched: &lt;script&gt;alert(1)&lt;/script&gt;</html>")
        self.assertFalse(r.matched)

    def test_raw_reflection_matches(self):
        r = signals.verify_xss({"q": "<script>alert(1)</script>"},
                               b"<html>you searched: <script>alert(1)</script></html>")
        self.assertTrue(r.matched)


class VerifyCsrf(unittest.TestCase):
    def test_rejection_status_does_not_match(self):
        r = signals.verify_csrf({}, b"HTTP/1.1 403 Forbidden\r\n\r\nCSRF token missing")
        self.assertFalse(r.matched)

    def test_success_with_rejection_wording_does_not_match(self):
        r = signals.verify_csrf({}, b"HTTP/1.1 200 OK\r\n\r\nError: invalid csrf token")
        self.assertFalse(r.matched)

    def test_success_without_rejection_wording_matches(self):
        r = signals.verify_csrf({}, b"HTTP/1.1 302 Found\r\nLocation: /account\r\n\r\n")
        self.assertTrue(r.matched)


class VerifySsrf(unittest.TestCase):
    def test_echoed_url_without_internal_content_does_not_match(self):
        r = signals.verify_ssrf({"url": "http://169.254.169.254/"},
                                b"<html>fetched: http://169.254.169.254/</html>")
        self.assertFalse(r.matched)

    def test_aws_metadata_key_matches(self):
        r = signals.verify_ssrf({"url": "http://169.254.169.254/latest/meta-data/"},
                                b'{"ami-id": "ami-0abc123", "instance-id": "i-0abc123"}')
        self.assertTrue(r.matched)

    def test_etc_passwd_content_matches(self):
        r = signals.verify_ssrf({"url": "file:///etc/passwd"}, b"root:x:0:0:root:/root:/bin/bash\n")
        self.assertTrue(r.matched)


class VerifySsti(unittest.TestCase):
    def test_literal_reflection_does_not_match(self):
        r = signals.verify_ssti({"name": "{{7*7}}"}, b"hello {{7*7}}")
        self.assertFalse(r.matched)

    def test_no_evaluated_value_does_not_match(self):
        r = signals.verify_ssti({"name": "{{7*7}}"}, b"hello there")
        self.assertFalse(r.matched)

    def test_evaluated_value_matches(self):
        r = signals.verify_ssti({"name": "{{7*7}}"}, b"hello 49")
        self.assertTrue(r.matched)


class VerifyXxe(unittest.TestCase):
    def test_entity_echoed_without_resolution_does_not_match(self):
        r = signals.verify_xxe({}, b"<foo>&xxe;</foo>")
        self.assertFalse(r.matched)

    def test_resolved_etc_passwd_matches(self):
        r = signals.verify_xxe({}, b"<foo>root:x:0:0:root:/root:/bin/bash</foo>")
        self.assertTrue(r.matched)


class VerifyCmdi(unittest.TestCase):
    def test_no_command_output_does_not_match(self):
        r = signals.verify_cmdi({"name": "test; id"}, b"<html>no such user</html>")
        self.assertFalse(r.matched)

    def test_id_command_output_matches(self):
        r = signals.verify_cmdi({"name": "test; id"}, b"uid=33(www-data) gid=33(www-data) groups=33(www-data)")
        self.assertTrue(r.matched)


class Check(unittest.TestCase):
    def test_unknown_class_returns_none(self):
        self.assertIsNone(signals.check({"q": "hello"}, b"hello"))

    def test_known_class_returns_vclass_and_result(self):
        vclass, outcome = signals.check({"q": "<script>x</script>"}, b"echo: <script>x</script>")
        self.assertEqual(vclass, "XSS")
        self.assertTrue(outcome.matched)

    def test_csrf_class_returns_vclass_and_result(self):
        vclass, outcome = signals.check({"request": _CSRF_REQUEST}, b"HTTP/1.1 200 OK\r\n\r\nupdated")
        self.assertEqual(vclass, "CSRF")
        self.assertTrue(outcome.matched)

    def test_ssrf_class_returns_vclass_and_result(self):
        vclass, outcome = signals.check({"url": "http://169.254.169.254/"}, b'"iam/security-credentials"')
        self.assertEqual(vclass, "SSRF")
        self.assertTrue(outcome.matched)


if __name__ == "__main__":
    unittest.main()
