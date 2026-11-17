"""fingerprint.py 순수 함수를 확인한다(UPGRADE_PLAN.md G1, H1 v2). DB·LLM 의존 없음 — 전부 단위 테스트.

H1: params 는 도구 인자 키가 아니라 실제 HTTP 매개변수 이름이다. 아래 원시 요청 형식은 Burp `send_http1_request`
의 `content` 인자, URL 형식은 Playwright `browser_navigate` 의 `url` 인자와 같은 모양이다.
"""
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


def _burp(method, target, body="", content_type=None, extra=""):
    head = f"{method} {target} HTTP/1.1\r\nHost: x.test\r\n" + (f"Content-Type: {content_type}\r\n" if content_type else "")
    return {"content": head + extra + "\r\n" + body, "targetHostname": "x.test", "targetPort": 443}


class Locate(unittest.TestCase):
    def test_raw_post_json_uses_json_key_path(self):
        args = _burp("POST", "/api/user?x=1", '{"user":{"name":"<script>alert(1)</script>"},"q":"ok"}',
                     "application/json")
        loc = fingerprint.locate(args)
        self.assertEqual((loc["method"], loc["path"]), ("POST", "/api/user"))
        self.assertEqual(loc["params"], ["user.name"])  # 도구 인자 키(content)가 아님

    def test_raw_get_request_is_recognised(self):
        """v1 은 CSRF 용 정규식(POST/PUT/DELETE/PATCH)을 빌려 써서 원시 GET 을 못 읽었다."""
        loc = fingerprint.locate(_burp("GET", "/item?id=1'%20OR%20'1'='1"))
        self.assertEqual(loc, {"method": "GET", "path": "/item", "params": ["id"]})

    def test_raw_form_cookie_header_multipart_and_xml_body(self):
        form = fingerprint.locate(_burp("POST", "/item", "id=1' OR '1'='1&x=2", "application/x-www-form-urlencoded"))
        self.assertEqual(form["params"], ["id"])
        cookie = fingerprint.locate(_burp("GET", "/p", extra="Cookie: sid=abc; pref=1'--\r\n"))
        self.assertEqual(cookie["params"], ["cookie:pref"])
        header = fingerprint.locate(_burp("GET", "/p", extra="X-Forwarded-For: 169.254.169.254\r\n"))
        self.assertEqual(header["params"], ["header:x-forwarded-for"])
        multipart_body = ('--B\r\nContent-Disposition: form-data; name="comment"\r\n\r\n<script>alert(1)</script>\r\n'
                          '--B\r\nContent-Disposition: form-data; name="ok"\r\n\r\nplain\r\n--B--')
        multipart = fingerprint.locate(_burp("POST", "/up", multipart_body, "multipart/form-data; boundary=B"))
        self.assertEqual(multipart["params"], ["comment"])
        xml = fingerprint.locate(_burp("POST", "/x", '<!DOCTYPE a [<!ENTITY e SYSTEM "file:///etc/passwd">]>',
                                       "text/xml"))
        self.assertEqual(xml["params"], ["body"])

    def test_url_arg_uses_query_param_names(self):
        loc = fingerprint.locate({"url": "http://x.test/search?q=' OR '1'='1&page=2"})
        self.assertEqual(loc, {"method": "GET", "path": "/search", "params": ["q"]})

    def test_structured_args_keep_their_key_names(self):
        loc = fingerprint.locate({"url": "http://x/item", "params": {"id": "1' OR '1'='1"}})
        self.assertEqual(loc["params"], ["id"])

    def test_param_order_does_not_matter(self):
        a = fingerprint.locate({"url": "http://x/p?b=<script>x</script>&a=' OR '1'='1"})
        b = fingerprint.locate({"url": "http://x/p?a=' OR '1'='1&b=<script>x</script>"})
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
        args1 = {"url": "http://x/user/1?q=' OR '1'='1"}
        args2 = {"url": "http://x/user/2?q=' OR '1'='1"}
        fp1, _, _ = fingerprint.compute("asset-1", "SQLI", args1)
        fp2, _, _ = fingerprint.compute("asset-1", "SQLI", args2)
        self.assertNotEqual(fp1, fp2)  # D1: 경로 원문 비교 — /user/1 과 /user/2 는 다른 finding

    def test_different_params_on_same_path_are_different(self):
        """H1 핵심: 같은 경로의 다른 매개변수는 자동 병합되면 안 된다(Burp·Playwright 형식 모두)."""
        for a, b in ((_burp("POST", "/item", "id=1' OR '1'='1", "application/x-www-form-urlencoded"),
                      _burp("POST", "/item", "name=1' OR '1'='1", "application/x-www-form-urlencoded")),
                     ({"url": "http://x/item?id=1' OR '1'='1"}, {"url": "http://x/item?name=1' OR '1'='1"})):
            self.assertNotEqual(fingerprint.compute("asset-1", "SQLI", a)[0],
                                fingerprint.compute("asset-1", "SQLI", b)[0])

    def test_same_param_across_tools_is_the_same(self):
        """Burp 원시 GET 과 Playwright URL 이 같은 매개변수를 때리면 같은 지점이다."""
        raw = fingerprint.compute("asset-1", "SQLI", _burp("GET", "/item?id=1'%20OR%20'1'='1"))[0]
        url = fingerprint.compute("asset-1", "SQLI", {"url": "http://x.test/item?id=1' OR '1'='1"})[0]
        self.assertEqual(raw, url)

    def test_no_identifiable_param_means_no_fingerprint(self):
        """모르면 합치지 않는다: 위치는 있으나 매개변수를 특정 못 하면 locator_hash 만."""
        fp, loc_hash, locator = fingerprint.compute("asset-1", "SQLI", {"url": "http://x/item?id=5"})
        self.assertIsNone(fp)
        self.assertIsNotNone(loc_hash)
        self.assertEqual(locator["params"], [])

    def test_csrf_is_located_without_params(self):
        fp, _, _ = fingerprint.compute("asset-1", "CSRF", _burp("POST", "/transfer", "amount=1",
                                                                  "application/x-www-form-urlencoded"))
        self.assertIsNotNone(fp)

    def test_same_locator_same_fingerprint(self):
        args = {"url": "http://x/login?u=' OR '1'='1"}
        fp1, _, _ = fingerprint.compute("asset-1", "SQLI", args)
        fp2, _, _ = fingerprint.compute("asset-1", "SQLI", args)
        self.assertEqual(fp1, fp2)


if __name__ == "__main__":
    unittest.main()
