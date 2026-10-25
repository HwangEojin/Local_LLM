"""자체 트래픽 레코딩 프록시(ARTEX 자체 MITM 프록시 대응 축소판, HTTP 만)를 실제 소켓으로 확인한다.

- 범위 안 호스트(baseline-demo="localhost")로 가는 요청은 실제로 전달되고 evidence 로 남는다.
- 범위 밖 호스트는 403으로 거부되고 evidence 는 안 남는다.
"""
import json
import socketserver
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import ProxyHandler, Request, build_opener

from audit.models import ConfigError
from diagnostic_workbench import config, db, proxy, runs


class _UpstreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"hello-from-upstream")

    def log_message(self, *a):
        pass


class ProxyDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg = config.load(None)
            cls.conn = db.connect(cls.cfg.dsn)
            db.require_migrated(cls.conn, cls.cfg.migrations_dir)
        except ConfigError as e:
            raise unittest.SkipTest(f"DB 연결/마이그레이션 불가: {e}") from None
        if "baseline-demo" not in {t["target_id"] for t in json.loads(
                (cls.cfg.home / "config" / "scope.json").read_text(encoding="utf-8"))["targets"]}:
            raise unittest.SkipTest("scope.json 에 baseline-demo 대상이 없음")

        cls.upstream = HTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        cls.upstream_port = cls.upstream.server_address[1]
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()

        wb_run = runs.create_run(cls.conn, cls.cfg, ["baseline-demo"], "pytest-proxy", checklist_id=None,
                                 use_llm=True)
        cls.conn.commit()
        cls.wb_run = wb_run  # TestCase.run() 과 이름이 겹치면 안 되므로 cls.run 이 아니라 cls.wb_run
        cls.ctx = runs.build_context(cls.cfg, cls.conn, wb_run)

        cls.proxy_server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), proxy._make_handler(cls.ctx))
        cls.proxy_port = cls.proxy_server.server_address[1]
        threading.Thread(target=cls.proxy_server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.proxy_server.shutdown()
        cls.proxy_server.server_close()
        cls.upstream.shutdown()
        cls.upstream.server_close()
        if hasattr(cls, "conn"):
            cls.conn.close()

    def _opener(self):
        return build_opener(ProxyHandler({"http": f"127.0.0.1:{self.proxy_port}"}))

    def test_in_scope_request_is_forwarded_and_recorded(self):
        before = self.conn.execute("SELECT count(*) AS n FROM evidence WHERE run_id=%s AND tool='proxy.http'",
                                   (self.wb_run["id"],)).fetchone()["n"]
        resp = self._opener().open(f"http://localhost:{self.upstream_port}/path", timeout=10)
        self.assertEqual(resp.read(), b"hello-from-upstream")
        after = self.conn.execute("SELECT count(*) AS n FROM evidence WHERE run_id=%s AND tool='proxy.http'",
                                  (self.wb_run["id"],)).fetchone()["n"]
        self.assertEqual(after, before + 1)

    def test_out_of_scope_host_rejected(self):
        req = Request("http://evil.example.invalid/x")
        with self.assertRaises(Exception) as ctxmgr:
            self._opener().open(req, timeout=10)
        self.assertIn("403", str(ctxmgr.exception))


if __name__ == "__main__":
    unittest.main()
