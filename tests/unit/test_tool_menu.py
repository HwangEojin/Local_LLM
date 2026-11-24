"""LLM 에 보이는 도구 메뉴(TOOLS)를 확인한다(UPGRADE_PLAN.md K 후속).

- 실행의 allowed_tools 밖 도구는 planner·worker 메뉴에서 빠진다(정책이 어차피 DENY 하므로 제안 낭비 방지).
- host 대상 실행에서도 url 인자 도구(http.request, net.http_head)가 planner 메뉴에 나온다 — 예전엔 범위 인자 종류를
  글자 그대로 비교해 통째로 빠졌다. path 인자 도구는 path 대상이 있을 때만.
"""
import json
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db, explore, prompts, runs


class ScopeKindsFitTest(unittest.TestCase):
    def test_truth_table(self):
        self.assertTrue(prompts.scope_kinds_fit(["url"], {"host"}))
        self.assertTrue(prompts.scope_kinds_fit(["host"], {"url"}))
        self.assertTrue(prompts.scope_kinds_fit([], {"host"}))
        self.assertFalse(prompts.scope_kinds_fit(["path"], {"host"}))
        self.assertFalse(prompts.scope_kinds_fit(["url"], {"path"}))
        self.assertTrue(prompts.scope_kinds_fit(["path", "url"], {"path", "host"}))

    def test_visible_tools_allowed_filter(self):
        tools = {"a": object(), "b": object()}
        self.assertEqual(set(prompts.visible_tools(tools, "planner", {})), {"a", "b"})
        self.assertEqual(set(prompts.visible_tools(tools, "planner", {}, ["b"])), {"b"})
        self.assertEqual(set(prompts.visible_tools(tools, "planner", {"hidden_from": {"b": ["planner"]}}, ["b"])), set())


class PlannerMenuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg = config.load(None)
            cls.conn = db.connect(cls.cfg.dsn)
            db.require_migrated(cls.conn, cls.cfg.migrations_dir)
        except ConfigError as e:
            raise unittest.SkipTest(f"DB 연결/마이그레이션 불가: {e}") from None
        scope = json.loads((cls.cfg.home / "config" / "scope.json").read_text(encoding="utf-8"))
        target = next((t for t in scope["targets"] if t["target_id"] == "baseline-demo"), None)
        if target is None or target["type"] != "host":
            raise unittest.SkipTest("scope.json 에 host 유형 baseline-demo 대상이 없음")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def _tools_in_prompt(self, allowed):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-tool-menu", checklist_id=None,
                              use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        ctx.run = {**ctx.run, "allowed_tools": allowed}
        text, _, _ = explore._context(ctx)
        line = next(ln for ln in text.splitlines() if ln.startswith("TOOLS"))
        return json.loads(line.split(":", 1)[1])

    def test_url_tools_offered_for_host_target_and_menu_limited_to_allowed(self):
        tools = self._tools_in_prompt(["http.request", "net.http_head"])
        self.assertEqual(set(tools), {"http.request", "net.http_head"})

    def test_all_tools_when_no_allowed_list(self):
        tools = self._tools_in_prompt(None)
        self.assertIn("http.request", tools)
        self.assertNotIn("fs.read_file", tools)  # path 인자 도구 — host 대상만 있는 실행에서는 항상 거부되므로 뺀다


if __name__ == "__main__":
    unittest.main()
