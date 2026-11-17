"""리다이렉트 범위 재검사(H5)·환경 변수 프록시 무시(H6)를 실제 소켓으로 확인한다(UPGRADE_PLAN.md H).

재현 시나리오: 범위 안 주소가 302 로 범위 밖 주소를 가리킨다. 고치기 전에는 진단 프록시와 net.http_head 가
범위 검사 없이 따라가 범위 밖 서버에 요청을 보냈다(2026-11-17 재현).

macOS 는 기본으로 127.0.0.2 에 바인딩할 수 없으므로, "범위 밖 호스트"는 같은 로컬 서버를 범위 대상
이름(localhost)이 아닌 다른 이름(127.0.0.1)으로 가리키는 방식으로 만든다 — 프록시의 범위 검사는 호스트
문자열 기준이다.
"""
import json
import os
import socketserver
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock
from urllib.request import ProxyHandler, build_opener

from audit import safe_http
from audit.adapters import net_tools
from audit.models import ConfigError
from diagnostic_workbench import config, db, proxy, runs


def _serve(handler_cls):
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _target_handler(hits: list):
    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OUT-OF-SCOPE-CONTENT")

        def do_HEAD(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass
    return Target


def _redirect_handler(location_fn):
    class Redir(BaseHTTPRequestHandler):
        def _redirect(self):
            self.send_response(302)
            self.send_header("Location", location_fn())
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            self._redirect()

        def do_HEAD(self):
            self._redirect()

        def log_message(self, *a):
            pass
    return Redir


class SafeOpener(unittest.TestCase):
    """DB 없이 도는 단위 검증: opener 자체와 내장 net.http_head."""

    def setUp(self):
        self.hits = []
        self.target = _serve(_target_handler(self.hits))
        tport = self.target.server_address[1]
        self.redir = _serve(_redirect_handler(lambda: f"http://127.0.0.1:{tport}/secret"))
        self.addCleanup(self.target.shutdown)
        self.addCleanup(self.redir.shutdown)

    def test_opener_does_not_follow_redirect(self):
        url = f"http://127.0.0.1:{self.redir.server_address[1]}/start"
        with self.assertRaises(urllib.error.HTTPError) as cm:
            safe_http.opener().open(url, timeout=5)
        self.assertEqual(cm.exception.code, 302)
        self.assertEqual(self.hits, [])  # 리다이렉트 목적지에는 요청이 하나도 안 갔다

    def test_opener_ignores_environment_proxy(self):
        # 존재하지 않는 프록시를 환경 변수로 지정해도 직접 연결해야 한다(H6).
        bogus = {"HTTP_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9",
                 "HTTPS_PROXY": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
                 "NO_PROXY": "", "no_proxy": ""}
        with mock.patch.dict(os.environ, bogus):
            with safe_http.opener().open(f"http://127.0.0.1:{self.target.server_address[1]}/direct",
                                         timeout=5) as r:
                self.assertEqual(r.read(), b"OUT-OF-SCOPE-CONTENT")
        self.assertEqual(self.hits, ["/direct"])

    def test_http_head_records_location_without_following(self):
        tool = net_tools.make_tools(100_000)["net.http_head"]
        out = tool.executor({"url": f"http://127.0.0.1:{self.redir.server_address[1]}/start"})
        result = json.loads(out.data)
        self.assertEqual(result["status"], 302)
        self.assertTrue(result["redirect_not_followed"].endswith("/secret"))
        self.assertEqual(self.hits, [])


class ProxyRedirectDB(unittest.TestCase):
    """실제 진단 프록시: 범위 안(localhost) 302 → 범위 밖(127.0.0.1) 목적지."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg = config.load(None)
            cls.conn = db.connect(cls.cfg.dsn)
            db.require_migrated(cls.conn, cls.cfg.migrations_dir)
        except ConfigError as e:
            raise unittest.SkipTest(f"DB 연결/마이그레이션 불가: {e}") from None
        targets = json.loads((cls.cfg.home / "config" / "scope.json").read_text(encoding="utf-8"))["targets"]
        if not any(t["target_id"] == "baseline-demo" and t["value"] == "localhost" for t in targets):
            raise unittest.SkipTest("scope.json 에 baseline-demo(localhost) 대상이 없음")

        cls.hits = []
        cls.target = _serve(_target_handler(cls.hits))
        tport = cls.target.server_address[1]
        cls.redir = _serve(_redirect_handler(lambda: f"http://127.0.0.1:{tport}/secret"))

        cls.wb_run = runs.create_run(cls.conn, cls.cfg, ["baseline-demo"], "pytest-redirect", checklist_id=None,
                                     use_llm=True)
        cls.conn.commit()
        cls.ctx = runs.build_context(cls.cfg, cls.conn, cls.wb_run)
        cls.proxy_server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), proxy._make_handler(cls.ctx))
        threading.Thread(target=cls.proxy_server.serve_forever, daemon=True).start()
        cls.proxy_url = f"127.0.0.1:{cls.proxy_server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        for s in ("proxy_server", "redir", "target"):
            if hasattr(cls, s):
                getattr(cls, s).shutdown()
                getattr(cls, s).server_close()
        if hasattr(cls, "conn"):
            cls.conn.close()

    def setUp(self):
        self.hits.clear()
        # 테스트 클라이언트 쪽 보호: urllib 의 ProxyHandler 는 프록시를 명시해도 환경 변수 no_proxy 에
        # localhost/127.0.0.1 이 있으면 프록시를 건너뛰고 직접 연결한다(진단 프록시를 아예 안 거침).
        patcher = mock.patch.dict(os.environ, {"no_proxy": "", "NO_PROXY": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _client(self, follow: bool):
        handlers = [ProxyHandler({"http": self.proxy_url})]
        if not follow:
            handlers.append(safe_http._NoRedirect())
        return build_opener(*handlers)

    def test_proxy_returns_3xx_and_never_contacts_out_of_scope_host(self):
        url = f"http://localhost:{self.redir.server_address[1]}/start"
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._client(follow=False).open(url, timeout=10)
        self.assertEqual(cm.exception.code, 302)
        self.assertTrue(cm.exception.headers["Location"].endswith("/secret"))
        self.assertEqual(self.hits, [])
        ev = self.conn.execute(
            "SELECT details FROM audit_events WHERE run_id=%s AND event='redirect_not_followed' "
            "ORDER BY id DESC LIMIT 1", (self.wb_run["id"],)).fetchone()
        self.assertIsNotNone(ev)
        self.assertFalse(ev["details"]["in_scope"])

    def test_client_following_location_is_rechecked_and_denied(self):
        # 클라이언트가 Location 을 따라가면 그 요청이 다시 프록시를 지나며 범위 검사를 받는다(HD3).
        url = f"http://localhost:{self.redir.server_address[1]}/start"
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._client(follow=True).open(url, timeout=10)
        self.assertEqual(cm.exception.code, 403)
        self.assertEqual(self.hits, [])

    def test_upstream_ignores_environment_proxy(self):
        # H6: 셸에 엉뚱한 프록시가 있어도 진단 프록시는 범위 안 대상에 직접 연결한다.
        bogus = {"HTTP_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9",
                 "NO_PROXY": "", "no_proxy": ""}
        with mock.patch.dict(os.environ, bogus):
            with self.assertRaises(urllib.error.HTTPError) as cm:  # 업스트림까지 갔다면 302 가 돌아온다
                self._client(follow=False).open(f"http://localhost:{self.redir.server_address[1]}/env", timeout=10)
        self.assertEqual(cm.exception.code, 302)
