"""자체 트래픽 레코딩 프록시(실제 TLS 가로채기 MITM)를 실제 소켓으로 확인한다.

- 범위 안 호스트(baseline-demo="localhost")로 가는 HTTP 요청은 실제로 전달되고 evidence 로 남는다.
- 범위 밖 호스트는 403으로 거부되고 evidence 는 안 남는다.
- HTTPS(CONNECT)는 실제로 복호화된다: 테스트가 직접 로컬 CA 를 신뢰하도록 SSLContext 를 구성해
  진짜 TLS 핸드셰이크를 완료하고, 복호화된 내용이 evidence(tool='proxy.https')로 남는 것까지 확인한다.
"""
import json
import socketserver
import ssl
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from audit.models import ConfigError
from diagnostic_workbench import config, db, proxy, runs, tls_ca


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


class ProxyHTTPSDB(unittest.TestCase):
    """실제 TLS 핸드셰이크로 HTTPS CONNECT 가 복호화돼 기록되는지 확인한다(블라인드 터널링이 아님)."""

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

        # 업스트림(실제 목적지)도 이 워크벤치의 CA 로 서명된 인증서로 TLS 를 띄운다 — 그러면
        # 프록시가 내부에서 다시 그 목적지로 연결할 때의 체인 검증도 같은 CA 신뢰로 통과시킬 수 있다.
        cert_path, key_path = tls_ca.leaf_cert_for(cls.cfg, "localhost")
        upstream_tls_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        upstream_tls_ctx.load_cert_chain(str(cert_path), str(key_path))
        cls.upstream = HTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        cls.upstream.socket = upstream_tls_ctx.wrap_socket(cls.upstream.socket, server_side=True)
        cls.upstream_port = cls.upstream.server_address[1]
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()

        wb_run = runs.create_run(cls.conn, cls.cfg, ["baseline-demo"], "pytest-proxy-https", checklist_id=None,
                                 use_llm=True)
        cls.conn.commit()
        cls.wb_run = wb_run
        cls.ctx = runs.build_context(cls.cfg, cls.conn, wb_run)

        cls.proxy_server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), proxy._make_handler(cls.ctx))
        cls.proxy_port = cls.proxy_server.server_address[1]
        threading.Thread(target=cls.proxy_server.serve_forever, daemon=True).start()

        # 프록시가 자신의 urlopen() 으로 업스트림(위 자체 서명 CA 인증서)에 다시 연결할 때도
        # 체인 검증을 통과해야 한다. urllib 의 전역 opener 는 처음 쓰일 때 한 번만 만들어져 그대로
        # 캐시되므로(HTTPSHandler 가 생성 시점에 기본 컨텍스트를 즉시 확정함 — ssl 모듈의 지연 평가가
        # 아님), ssl._create_default_https_context 를 바꾸는 것만으로는 이미 만들어진 전역 opener 에
        # 영향을 줄 수 없다. install_opener() 로 이 CA 를 신뢰하는 opener 를 직접 설치해 확실히 쓰게 한다.
        cls._orig_opener = urllib.request._opener
        trust_ctx = ssl.create_default_context(cafile=str(tls_ca.ca_cert_path(cls.cfg)))
        urllib.request.install_opener(build_opener(HTTPSHandler(context=trust_ctx)))

    @classmethod
    def tearDownClass(cls):
        urllib.request._opener = cls._orig_opener
        cls.proxy_server.shutdown()
        cls.proxy_server.server_close()
        cls.upstream.shutdown()
        cls.upstream.server_close()
        if hasattr(cls, "conn"):
            cls.conn.close()

    def test_https_connect_is_decrypted_and_recorded(self):
        before = self.conn.execute("SELECT count(*) AS n FROM evidence WHERE run_id=%s AND tool='proxy.https'",
                                   (self.wb_run["id"],)).fetchone()["n"]
        client_ctx = ssl.create_default_context(cafile=str(tls_ca.ca_cert_path(self.cfg)))
        opener = build_opener(ProxyHandler({"https": f"127.0.0.1:{self.proxy_port}"}),
                              HTTPSHandler(context=client_ctx))
        resp = opener.open(f"https://localhost:{self.upstream_port}/path", timeout=10)
        self.assertEqual(resp.read(), b"hello-from-upstream")
        after = self.conn.execute("SELECT count(*) AS n FROM evidence WHERE run_id=%s AND tool='proxy.https'",
                                  (self.wb_run["id"],)).fetchone()["n"]
        self.assertEqual(after, before + 1)


if __name__ == "__main__":
    unittest.main()
