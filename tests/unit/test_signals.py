"""자동 점검 (B) 검증 신호: classify_payload/verify_sqli/verify_xss 가 반사·오류 문구 하나만으로 확정하지
않고, 인과관계(보낸 페이로드가 실제로 응답에 있는지)와 문맥(인코딩 여부, 엔진 특정 오류)을 함께 보는지 확인.
"""
import unittest

from audit import signals


class ClassifyPayload(unittest.TestCase):
    def test_no_known_signature_returns_none(self):
        self.assertIsNone(signals.classify_payload({"q": "hello world"}))

    def test_detects_xss_signature_in_nested_args(self):
        self.assertEqual(signals.classify_payload({"body": {"q": "<script>alert(1)</script>"}}), "XSS")

    def test_detects_sqli_signature(self):
        self.assertEqual(signals.classify_payload({"q": "' OR '1'='1"}), "SQLI")


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


class Check(unittest.TestCase):
    def test_unknown_class_returns_none(self):
        self.assertIsNone(signals.check({"q": "hello"}, b"hello"))

    def test_known_class_returns_vclass_and_result(self):
        vclass, outcome = signals.check({"q": "<script>x</script>"}, b"echo: <script>x</script>")
        self.assertEqual(vclass, "XSS")
        self.assertTrue(outcome.matched)


if __name__ == "__main__":
    unittest.main()
