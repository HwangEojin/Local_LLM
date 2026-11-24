"""K: http.request 가 Worker 경로(run_tool → 증거 → fact)에서 쓰일 때를 실제 PostgreSQL 로 확인한다.

- 본 증거 = 응답, 보낸 요청 = derived_from 으로 이어진 파생 증거(requirement_id 'explore:request').
- 응답에 되돌아온 세션 값(알려진 비밀)은 LLM 이 보는 fact 발췌에서 가려진다. 증거 원본 파일은 그대로(KD4).
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from psycopg.types.json import Jsonb

from audit.models import ConfigError
from diagnostic_workbench import config, db, evidence, http_tool, runs, worker

_LEAK = "leaked-session-value-xyz"


class _Echo(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = f"<html>token={_LEAK}</html>".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class HttpRequestWorkerTest(unittest.TestCase):
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
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "server"):
            cls.server.shutdown()
        if hasattr(cls, "conn"):
            cls.conn.close()

    def test_response_evidence_request_derived_and_excerpt_redacted(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-http-request",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        asset_id = next(iter(ctx.targets))
        if ctx.targets[asset_id].value.lower() != "localhost":
            self.skipTest("baseline-demo 대상이 localhost 가 아님")
        http_tool.store_for(self.cfg)._secrets.add(_LEAK)  # 세션 로그인으로 알게 된 값이라고 치자
        node = self.conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'intent','test','{}') RETURNING id",
            (run["id"],)).fetchone()["id"]
        url = f"http://localhost:{self.server.server_address[1]}/page"
        item_id = self.conn.execute(
            "INSERT INTO backlog_items (run_id, kind, exploration_node_id, asset_id, idempotency_key, reason, payload, "
            "status) VALUES (%s,'exploration',%s,%s,%s,'테스트',%s,'RUNNING') RETURNING id",
            (run["id"], node, asset_id, f"test-http-{node}", Jsonb({"tool": "http.request", "args": {"url": url}}))
        ).fetchone()["id"]
        it = worker.load_item(ctx, dict(self.conn.execute("SELECT * FROM backlog_items WHERE id=%s",
                                                          (item_id,)).fetchone()))
        eid, out, payload = worker._explore_step(ctx, it, None, 1, "http.request", {"url": url})
        self.assertIsNotNone(out)
        main = self.conn.execute("SELECT * FROM evidence WHERE id=%s", (eid,)).fetchone()
        self.assertTrue(evidence.read_bytes(main, self.cfg.evidence_dir).startswith(b"HTTP/1.1 200"))
        self.assertIn(_LEAK.encode(), evidence.read_bytes(main, self.cfg.evidence_dir))  # KD4: 원본 그대로
        derived = self.conn.execute("SELECT * FROM evidence WHERE derived_from=%s", (eid,)).fetchall()
        self.assertEqual([d["requirement_id"] for d in derived], ["explore:request"])
        self.assertIn(b"GET /page HTTP/1.1", evidence.read_bytes(derived[0], self.cfg.evidence_dir))
        self.assertNotIn(_LEAK, payload["excerpt"])  # LLM 이 보는 발췌에서는 가림


if __name__ == "__main__":
    unittest.main()
