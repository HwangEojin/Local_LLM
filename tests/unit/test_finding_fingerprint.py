"""fingerprint.py 순수 함수를 확인한다(UPGRADE_PLAN.md G1). DB·LLM 의존 없음 — 전부 단위 테스트."""
import unittest

from diagnostic_workbench import fingerprint


class StripQuery(unittest.TestCase):
    def test_removes_query_string_only(self):
        self.assertEqual(fingerprint.strip_query("/a?x=1&y=2"), "/a")
        self.assertEqual(fingerprint.strip_query("/a"), "/a")

    def test_path_is_kept_verbatim(self):
        """D1: 쿼리스트링만 떼고 나머지는 원문 유지 — 대소문자·끝 슬래시·퍼센트 인코딩 전부 그대로."""
        self.assertNotEqual(fingerprint.strip_query("/Admin"), fingerprint.strip_query("/admin"))
        self.assertNotEqual(fingerprint.strip_query("/a/"), fingerprint.strip_query("/a"))
        self.assertEqual(fingerprint.strip_query("/user%2Fprofile"), "/user%2Fprofile")  # %2F 미해석


class Locate(unittest.TestCase):
    def test_from_raw_http_request(self):
        # param_names 는 signals.injection_params() 가 찾는 "페이로드를 담은 args 키 이름들"이다(요청 본문을
        # 파싱해 필드명을 뽑는 게 아니라, 이 호출 인자 중 뭐가 악성 페이로드를 담았는지만 본다).
        args = {"request": "POST /api/user?x=1 HTTP/1.1\r\nHost: x\r\n\r\n{\"name\":\"<script>alert(1)</script>\"}"}
        loc = fingerprint.locate(args)
        self.assertEqual(loc["method"], "POST")
        self.assertEqual(loc["path"], "/api/user")
        self.assertIn("request", loc["params"])

    def test_from_url_arg(self):
        loc = fingerprint.locate({"url": "http://x.test/search?q=' OR '1'='1"})
        self.assertEqual(loc["method"], "GET")
        self.assertEqual(loc["path"], "/search")
        self.assertIn("url", loc["params"])

    def test_param_order_does_not_matter(self):
        a = fingerprint.locate({"url": "http://x/p", "b": "<script>x</script>", "a": "' OR '1'='1"})
        b = fingerprint.locate({"url": "http://x/p", "a": "' OR '1'='1", "b": "<script>x</script>"})
        self.assertEqual(a["params"], b["params"])  # 둘 다 정렬된 리스트

    def test_extraction_failure_returns_none(self):
        self.assertIsNone(fingerprint.locate({}))
        self.assertIsNone(fingerprint.locate({"foo": "bar"}))
        self.assertIsNone(fingerprint.locate("not a dict"))


class Compute(unittest.TestCase):
    def test_known_signal_class_gets_fingerprint(self):
        fp, loc_hash, locator = fingerprint.compute("asset-1", "SQLI", {"url": "http://x/login?u=' OR '1'='1"})
        self.assertIsNotNone(fp)
        self.assertIsNotNone(loc_hash)
        self.assertEqual(locator["path"], "/login")

    def test_other_class_has_locator_hash_but_no_fingerprint(self):
        """D2: OTHER 는 '중복 의심' 용 locator_hash 만 만들고 자동 병합용 fingerprint 는 만들지 않는다."""
        fp, loc_hash, locator = fingerprint.compute("asset-1", "OTHER", {"url": "http://x/profile"})
        self.assertIsNone(fp)
        self.assertIsNotNone(loc_hash)

    def test_extraction_failure_gives_none_for_both(self):
        fp, loc_hash, locator = fingerprint.compute("asset-1", "SQLI", {})
        self.assertIsNone(fp)
        self.assertIsNone(loc_hash)
        self.assertEqual(locator, {})

    def test_different_asset_or_path_changes_fingerprint(self):
        args1 = {"url": "http://x/user/1"}
        args2 = {"url": "http://x/user/2"}
        fp1, _, _ = fingerprint.compute("asset-1", "SQLI", args1)
        fp2, _, _ = fingerprint.compute("asset-1", "SQLI", args2)
        self.assertNotEqual(fp1, fp2)  # D1: 경로 원문 비교 — /user/1 과 /user/2 는 다른 finding

    def test_same_locator_same_fingerprint(self):
        args = {"url": "http://x/login?u=' OR '1'='1"}
        fp1, _, _ = fingerprint.compute("asset-1", "SQLI", args)
        fp2, _, _ = fingerprint.compute("asset-1", "SQLI", args)
        self.assertEqual(fp1, fp2)


if __name__ == "__main__":
    unittest.main()
