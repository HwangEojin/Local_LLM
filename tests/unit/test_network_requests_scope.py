"""리다이렉트 하위 요청 범위 검사(README §7, 2026-10-19 구현)를 확인한다.

browser_network_requests 가 돌려주는 실제 출력 형식(@playwright/mcp packages/playwright-core/src/tools/
backend/network.ts 의 renderRequestLine: "N. [METHOD] url => [STATUS] statusText")을 그대로 합성해
확인한다 — 이 PC 에는 Playwright MCP 가 설치돼 있지 않아 실제 서버로는 확인할 수 없음.
"""
import json
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db, policy, runs


def _mcp_payload(text: str) -> bytes:
    return json.dumps({"content": [{"type": "text", "text": text}]}).encode()


class NetworkRequestsScopeTest(unittest.TestCase):
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

    def _ctx_and_asset(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-network-scope",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        asset_id = self.conn.execute(
            "SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        return ctx, asset_id

    def test_in_scope_sub_requests_pass(self):
        ctx, asset_id = self._ctx_and_asset()
        text = (
            "### Result\nNetwork\n"
            "1. [GET] http://localhost:9999/ => [200] OK\n"
            "2. [GET] http://localhost:9999/style.css => [200] OK\n"
            "\n### Page state\n- Page URL: http://localhost:9999/\n- Page Title: demo\n"
        )
        err = policy.landed_out_of_scope(ctx, "mcp:playwright/browser_network_requests",
                                         _mcp_payload(text), asset_id)
        self.assertIsNone(err)

    def test_out_of_scope_sub_request_is_caught(self):
        ctx, asset_id = self._ctx_and_asset()
        text = (
            "### Result\nNetwork\n"
            "1. [GET] http://localhost:9999/ => [200] OK\n"
            "2. [GET] https://attacker-tracker.example.com/beacon.js => [200] OK\n"
            "\n### Page state\n- Page URL: http://localhost:9999/\n"
        )
        err = policy.landed_out_of_scope(ctx, "mcp:playwright/browser_network_requests",
                                         _mcp_payload(text), asset_id)
        self.assertIsNotNone(err)
        self.assertIn("하위 요청 중", err)

    def test_failed_sub_request_line_is_still_parsed(self):
        ctx, asset_id = self._ctx_and_asset()
        text = (
            "### Result\nNetwork\n"
            "1. [GET] https://attacker-tracker.example.com/x => [FAILED] net::ERR_FAILED\n"
            "\n### Page state\n- Page URL: http://localhost:9999/\n"
        )
        err = policy.landed_out_of_scope(ctx, "mcp:playwright/browser_network_requests",
                                         _mcp_payload(text), asset_id)
        self.assertIsNotNone(err)

    def test_other_playwright_tools_do_not_parse_network_lines(self):
        # 같은 숫자 목록 형식(네트워크 요청 줄)이 browser_navigate 같은 다른 도구 출력에 우연히 있어도
        # browser_network_requests 가 아니면 하위 요청으로 해석하지 않는다 — Page URL(최상위 탐색 결과)
        # 만 범위 안이면 통과한다.
        ctx, asset_id = self._ctx_and_asset()
        text = ("1. [GET] https://attacker-tracker.example.com/x => [200] OK\n"
                "- Page URL: http://localhost:9999/\n")
        err = policy.landed_out_of_scope(ctx, "mcp:playwright/browser_navigate", _mcp_payload(text), asset_id)
        self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()
