"""Burp 이력 기반 자산 추출(README §7, 2026-10-19 구현)을 확인한다.

- audit.adapters.burp.request_urls(): 실제 확인된 Burp MCP 응답 형식(HttpRequestResponse{...}, 줄바꿈
  없는 JSON 문자열 하나 — test_signals.VerifyCsrf.test_status_line_matches_inside_burp_json_wrapper 와
  같은 샘플)에서 요청 줄 + Host 헤더로 URL 을 복원한다. 구조화된 항목(url/baseUrl 키)도 그대로 쓴다.
- worker._observe_assets(): scope_filter(post)가 이미 걸러준 Burp 이력 결과에서 자산(url)과
  edge(asset_edges, rel='serves')를 자산 그래프에 등록한다.
- 범위 밖 URL은 등록되지 않는다(이중 확인 — scope_filter 는 호스트만, 여기선 전체 URL 로 다시 확인).
"""
import json
import unittest

from audit.adapters import burp
from audit.models import ConfigError, ToolOutput
from diagnostic_workbench import config, db, runs, worker


class RequestUrlsTest(unittest.TestCase):
    def test_real_burp_wrapper_format_recovers_url(self):
        item = ("HttpRequestResponse{httpRequest=GET /x HTTP/1.1\\r\\nHost: localhost:9999\\r\\n, "
                "httpResponse=HTTP/1.1 200 \\r\\n\\r\\n<html>ok</html>}")
        self.assertEqual(burp.request_urls(item), {"http://localhost:9999/x"})

    def test_structured_item_with_url_key(self):
        self.assertEqual(burp.request_urls({"url": "https://localhost:9999/api/users"}),
                         {"https://localhost:9999/api/users"})

    def test_nested_request_responses(self):
        item = {"requestResponses": [
            {"url": "http://localhost:9999/a"},
            "HttpRequestResponse{httpRequest=GET /b HTTP/1.1\\r\\nHost: localhost:9999\\r\\n, httpResponse=...}",
        ]}
        self.assertEqual(burp.request_urls(item), {"http://localhost:9999/a", "http://localhost:9999/b"})

    def test_no_request_line_or_host_returns_empty(self):
        self.assertEqual(burp.request_urls("random text with no http request line"), set())
        self.assertEqual(burp.request_urls(42), set())


class ObserveAssetsBurpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg = config.load(None)
            cls.conn = db.connect(cls.cfg.dsn)
            db.require_migrated(cls.conn, cls.cfg.migrations_dir)
        except ConfigError as e:
            raise unittest.SkipTest(f"DB 연결/마이그레이션 불가: {e}") from None
        if "baseline-demo" not in {t["target_id"] for t in __import__("json").loads(
                (cls.cfg.home / "config" / "scope.json").read_text(encoding="utf-8"))["targets"]}:
            raise unittest.SkipTest("scope.json 에 baseline-demo 대상이 없음")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def _ctx_and_item(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-burp-assets",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        asset_id = self.conn.execute(
            "SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        it = {"asset_id": asset_id, "target": ctx.targets[asset_id]}
        return ctx, it

    def test_history_items_register_url_assets_and_edges(self):
        ctx, it = self._ctx_and_item()
        payload = {"isError": False, "scope_filter": {}, "items": [
            {"url": "http://localhost:9999/login"},
            "HttpRequestResponse{httpRequest=GET /admin HTTP/1.1\\r\\nHost: localhost:9999\\r\\n, "
            "httpResponse=HTTP/1.1 200 \\r\\n\\r\\nok}",
        ]}
        out = ToolOutput(json.dumps(payload).encode(), "burp/mcp")
        worker._observe_assets(ctx, it, "mcp:burp/get_proxy_http_history", {"count": 10, "offset": 0},
                               out, "00000000-0000-0000-0000-000000000000")
        urls = {r["value"] for r in self.conn.execute(
            "SELECT value FROM assets WHERE kind='url' AND value LIKE 'http://localhost:9999/%'").fetchall()}
        self.assertIn("http://localhost:9999/login", urls)
        self.assertIn("http://localhost:9999/admin", urls)
        n = self.conn.execute(
            "SELECT count(*) AS n FROM asset_edges e JOIN assets a ON a.id = e.dst_id "
            "WHERE e.src_id=%s AND e.rel='serves' AND a.value LIKE 'http://localhost:9999/%%'",
            (it["asset_id"],)).fetchone()["n"]
        self.assertGreaterEqual(n, 2)

    def test_out_of_scope_url_is_not_registered(self):
        ctx, it = self._ctx_and_item()
        payload = {"isError": False, "scope_filter": {}, "items": [
            {"url": "http://attacker-tracker.example.com/beacon"},
        ]}
        out = ToolOutput(json.dumps(payload).encode(), "burp/mcp")
        worker._observe_assets(ctx, it, "mcp:burp/get_proxy_http_history", {"count": 10, "offset": 0},
                               out, "00000000-0000-0000-0000-000000000000")
        row = self.conn.execute(
            "SELECT 1 FROM assets WHERE kind='url' AND value='http://attacker-tracker.example.com/beacon'"
        ).fetchone()
        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
