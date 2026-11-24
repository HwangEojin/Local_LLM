"""K: 자체 HTTP 요청 도구(http.request)와 인증 세션(UPGRADE_PLAN.md K)을 로컬 HTTP 서버로 확인한다.

- 증거 본문은 응답만, 보낸 요청은 파생 증거(attachments) — 요청의 페이로드가 '반사'로 오판되지 않는다.
- Host 는 url 로 고정(헤더·raw 로 못 바꿈), 3xx 는 따라가지 않는다, raw 의 절대 대상이 다른 호스트면 거부.
- 세션: static(환경 변수 쿠키)·form(로그인 후 Set-Cookie)·json(토큰 헤더), 쿠키 재발급 반영, 세션 대상 범위 밖
  URL 거부, 환경 변수 누락 거부. 보낸 요청 증거에는 세션 값이 그대로(KD4), 비밀번호는 안 남음.
- 기존 파서 연동: fingerprint v2 매개변수, XSS 신호, CSRF 분류, 쿠키 헤더 마스킹.
설정 홈은 임시 폴더라 저장소의 config/ 는 바뀌지 않는다.
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from audit import signals
from audit.models import PolicyDenied, ToolError
from audit.policy import Policy, mask_secrets
from diagnostic_workbench import fingerprint, http_tool


class _Handler(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):  # 테스트 출력 조용히
        pass

    def _reply(self, status, body=b"", headers=()):
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, body=b""):
        _Handler.seen.append({"method": self.command, "path": self.path, "host": self.headers.get("Host"),
                              "cookie": self.headers.get("Cookie"), "auth": self.headers.get("Authorization"),
                              "body": body})

    def do_GET(self):
        self._record()
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/echo":
            self._reply(200, f"<html>you said {q.get('q', [''])[0]}</html>".encode(), [("Content-Type", "text/html")])
        elif u.path == "/static":
            self._reply(200, b"<html>nothing reflected</html>", [("Content-Type", "text/html")])
        elif u.path == "/redirect":
            self._reply(302, b"", [("Location", "/target")])
        elif u.path == "/me":
            who = self.headers.get("Authorization") or self.headers.get("Cookie")
            self._reply(200 if who else 401, f"who={who}".encode())
        elif u.path == "/rotate":
            self._reply(200, b"rotated", [("Set-Cookie", "sid=rotated-sid-value; Path=/; HttpOnly")])
        else:
            self._reply(404, b"no")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._record(body)
        if self.path == "/login":
            form = urllib.parse.parse_qs(body.decode())
            if form.get("username") == ["alice"] and form.get("password") == ["pw-alice-123"]:
                self._reply(302, b"", [("Location", "/home"), ("Set-Cookie", "sid=form-sid-secret; Path=/")])
            else:
                self._reply(401, b"bad")
        elif self.path == "/api/login":
            data = json.loads(body or b"{}")
            ok = data == {"email": "bob@example.test", "password": "pw-bob-456"}
            self._reply(200 if ok else 401, json.dumps({"data": {"token": "tok-bob-secret"}} if ok else {}).encode(),
                        [("Content-Type", "application/json")])
        else:
            self._reply(200, b"posted:" + body)


class HttpRequestToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _Handler.seen.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / "config").mkdir()
        scope = {"engagement_id": "TEST-K", "approved_by": "pytest", "valid_until": "2099-12-31",
                 "targets": [{"target_id": "local", "type": "host", "value": "127.0.0.1"},
                             {"target_id": "other", "type": "host", "value": "other.invalid"}]}
        (self.home / "config" / "scope.json").write_text(json.dumps(scope), encoding="utf-8")

    def _tool(self, sessions=None):
        if sessions is not None:
            (self.home / "config" / "auth_sessions.json").write_text(json.dumps({"sessions": sessions}),
                                                                     encoding="utf-8")
        cfg = SimpleNamespace(home=self.home, upstream_proxy=None,
                              settings=SimpleNamespace(limits={"max_evidence_bytes": 100_000}))
        return http_tool.make_tool(cfg), http_tool.store_for(cfg)

    def _sent(self, out) -> str:
        label, mime, ext, raw = out.attachments[0]
        self.assertEqual((label, mime), ("request", "message/http"))
        return raw.decode()

    def test_response_only_in_data_and_request_as_attachment(self):
        spec, _ = self._tool()
        out = spec.executor({"url": f"{self.base}/echo?q=hello"})
        data = out.data.decode()
        self.assertTrue(data.startswith("HTTP/1.1 200"))
        self.assertIn("you said hello", data)
        self.assertNotIn("GET /echo", data)  # 요청은 본 증거에 없다
        self.assertIn("GET /echo?q=hello HTTP/1.1", self._sent(out))

    def test_host_header_is_forced_from_url(self):
        spec, _ = self._tool()
        spec.executor({"url": f"{self.base}/echo", "headers": {"Host": "evil.example"}})
        spec.executor({"url": f"{self.base}/x", "raw": "GET /echo HTTP/1.1\r\nHost: evil.example\r\n\r\n"})
        self.assertEqual({s["host"] for s in _Handler.seen}, {f"127.0.0.1:{self.port}"})

    def test_raw_absolute_target_to_other_host_is_rejected(self):
        spec, _ = self._tool()
        with self.assertRaises(ToolError):
            spec.executor({"url": f"{self.base}/", "raw": "GET http://evil.example/ HTTP/1.1\r\n\r\n"})
        self.assertEqual(_Handler.seen, [])

    def test_redirect_is_not_followed(self):
        spec, _ = self._tool()
        out = spec.executor({"url": f"{self.base}/redirect"})
        self.assertTrue(out.data.decode().startswith("HTTP/1.1 302"))
        self.assertIn("Location: /target", out.data.decode())
        self.assertEqual([s["path"] for s in _Handler.seen], ["/redirect"])

    def test_post_body_and_raw_form(self):
        spec, _ = self._tool()
        out = spec.executor({"url": f"{self.base}/submit", "method": "POST", "body": "a=1",
                             "headers": {"Content-Type": "application/x-www-form-urlencoded"}})
        self.assertIn("posted:a=1", out.data.decode())
        out = spec.executor({"url": f"{self.base}/", "raw": "POST /submit HTTP/1.1\r\nContent-Type: text/plain\r\n\r\nraw-body"})
        self.assertIn("posted:raw-body", out.data.decode())

    def test_policy_rejects_out_of_scope_url(self):
        spec, _ = self._tool()
        policy = Policy(json.loads((self.home / "config" / "scope.json").read_text()), self.home)
        with self.assertRaises(PolicyDenied):
            policy.authorize(spec, "http.request", {"url": "http://evil.example/"}, policy.target("local"))
        policy.authorize(spec, "http.request", {"url": f"{self.base}/echo"}, policy.target("local"))

    def test_static_session_cookie_is_attached_and_kept_raw_in_sent_request(self):
        spec, store = self._tool([{"name": "a", "target_id": "local", "type": "static",
                                   "headers_from": {"Cookie": "WB_TEST_K_COOKIE"}}])
        with mock.patch.dict(os.environ, {"WB_TEST_K_COOKIE": "sid=static-secret-value"}):
            out = spec.executor({"url": f"{self.base}/me", "session": "a"})
        self.assertIn("who=sid=static-secret-value", out.data.decode())
        self.assertIn("static-secret-value", self._sent(out))  # KD4: 보낸 요청 증거는 원본 그대로
        self.assertIn("static-secret-value", store.known_secrets())  # LLM 발췌에서는 가릴 값으로 등록
        self.assertEqual(spec.args_schema["properties"]["session"], {"enum": ["a"]})

    def test_form_login_then_session_cookie(self):
        spec, store = self._tool([{"name": "alice", "target_id": "local", "type": "form",
                                   "login_url": f"{self.base}/login",
                                   "fields": {"username": "{{username}}", "password": "{{password}}"},
                                   "username_from": "WB_TEST_K_USER", "password_from": "WB_TEST_K_PW"}])
        with mock.patch.dict(os.environ, {"WB_TEST_K_USER": "alice", "WB_TEST_K_PW": "pw-alice-123"}):
            out = spec.executor({"url": f"{self.base}/me", "session": "alice"})
            spec.executor({"url": f"{self.base}/me", "session": "alice"})
        self.assertIn("who=sid=form-sid-secret", out.data.decode())
        sent = self._sent(out)
        self.assertIn("로그인 (form)", sent)
        self.assertNotIn("pw-alice-123", sent)  # 로그인 요청 본문(자격 증명)은 증거에 안 남김
        self.assertEqual([s["path"] for s in _Handler.seen], ["/login", "/me", "/me"])  # 로그인은 한 번
        self.assertIn("pw-alice-123", store.known_secrets())

    def test_json_login_token_header(self):
        spec, _ = self._tool([{"name": "bob", "target_id": "local", "type": "json",
                               "login_url": f"{self.base}/api/login",
                               "fields": {"email": "{{username}}", "password": "{{password}}"},
                               "username_from": "WB_TEST_K_USER", "password_from": "WB_TEST_K_PW",
                               "token_path": "data.token"}])
        with mock.patch.dict(os.environ, {"WB_TEST_K_USER": "bob@example.test", "WB_TEST_K_PW": "pw-bob-456"}):
            out = spec.executor({"url": f"{self.base}/me", "session": "bob"})
        self.assertIn("who=Bearer tok-bob-secret", out.data.decode())

    def test_failed_login_is_tool_error(self):
        spec, _ = self._tool([{"name": "alice", "target_id": "local", "type": "form",
                               "login_url": f"{self.base}/login", "fields": {"username": "{{username}}",
                                                                              "password": "{{password}}"},
                               "username_from": "WB_TEST_K_USER", "password_from": "WB_TEST_K_PW"}])
        with mock.patch.dict(os.environ, {"WB_TEST_K_USER": "alice", "WB_TEST_K_PW": "wrong"}), \
                self.assertRaises(ToolError):
            spec.executor({"url": f"{self.base}/me", "session": "alice"})

    def test_rotated_cookie_is_used_next_time(self):
        spec, _ = self._tool([{"name": "a", "target_id": "local", "type": "static",
                               "headers_from": {"Cookie": "WB_TEST_K_COOKIE"}}])
        with mock.patch.dict(os.environ, {"WB_TEST_K_COOKIE": "sid=old"}):
            spec.executor({"url": f"{self.base}/rotate", "session": "a"})
            out = spec.executor({"url": f"{self.base}/me", "session": "a"})
        self.assertIn("sid=rotated-sid-value", out.data.decode())

    def test_session_outside_its_target_scope_is_rejected(self):
        spec, _ = self._tool([{"name": "o", "target_id": "other", "type": "static",
                               "headers_from": {"Cookie": "WB_TEST_K_COOKIE"}}])
        with mock.patch.dict(os.environ, {"WB_TEST_K_COOKIE": "sid=x"}), self.assertRaises(ToolError):
            spec.executor({"url": f"{self.base}/me", "session": "o"})
        self.assertEqual(_Handler.seen, [])  # 세션 값을 범위 밖 대상에 보내기 전에 막는다

    def test_missing_env_var_is_tool_error(self):
        spec, _ = self._tool([{"name": "a", "target_id": "local", "type": "static",
                               "headers_from": {"Cookie": "WB_TEST_K_MISSING"}}])
        with mock.patch.dict(os.environ, {}, clear=False), self.assertRaises(ToolError):
            os.environ.pop("WB_TEST_K_MISSING", None)
            spec.executor({"url": f"{self.base}/me", "session": "a"})

    def test_xss_signal_sees_response_not_request(self):
        spec, _ = self._tool()
        probe = "<script>alert(1)</script>"
        reflect = {"url": f"{self.base}/echo?q={urllib.parse.quote(probe)}", "method": "GET"}
        static = {"url": f"{self.base}/static?q={urllib.parse.quote(probe)}", "method": "GET"}
        self.assertTrue(signals.verify_xss({"q": probe}, spec.executor(reflect).data).matched)
        # 반사하지 않는 페이지: 요청에 페이로드가 있어도 증거 본문(응답)에는 없으므로 일치하지 않아야 한다
        self.assertFalse(signals.verify_xss({"q": probe}, spec.executor(static).data).matched)

    def test_fingerprint_uses_real_parameter_names(self):
        args = {"url": f"{self.base}/login", "method": "POST", "body": "user=admin'--&remember=1",
                "headers": {"Content-Type": "application/x-www-form-urlencoded"}}
        self.assertEqual(fingerprint.locate(args), {"method": "POST", "path": "/login", "params": ["user"]})

    def test_csrf_classification_for_session_request_with_forged_origin(self):
        args = {"url": f"{self.base}/transfer", "method": "POST", "session": "a",
                "headers": {"Origin": "http://attacker.example"}, "body": "amount=1"}
        self.assertTrue(signals.classify_csrf(args))
        self.assertFalse(signals.classify_csrf({**args, "headers": {"Origin": self.base}}))

    def test_mask_secrets_covers_cookie_and_authorization(self):
        text = 'Set-Cookie: sid=abc123; Path=/\r\nCookie: a=b\r\nAuthorization: Basic Zm9vOmJhcg==\r\n{"Cookie": "x=y"}'
        masked = mask_secrets(text)
        for secret in ("abc123", "a=b", "Zm9vOmJhcg==", "x=y"):
            self.assertNotIn(secret, masked)


if __name__ == "__main__":
    unittest.main()
