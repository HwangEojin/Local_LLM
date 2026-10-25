"""트래픽/도구 호출 인터셉트 규칙을 확인한다.

규칙에 매치하면 위험도(read 포함)·자율 모드(full 포함)와 무관하게 ALLOW_WITH_APPROVAL 로 강제된다.
설정 파일이 없으면(기본) 아무 영향도 없다 — 기존 동작 그대로.
"""
import json
import re
import tempfile
import unittest
from pathlib import Path

from audit import config as audit_config
from audit.models import ConfigError, ToolOutput, ToolSpec
from diagnostic_workbench import config, db, policy, runs


class LoadInterceptRules(unittest.TestCase):
    def test_missing_file_means_no_rules(self):
        self.assertEqual(audit_config.load_intercept_rules(Path(tempfile.mkdtemp())), [])

    def test_rejects_invalid_regex(self):
        home = Path(tempfile.mkdtemp())
        (home / "config").mkdir()
        (home / "config" / "intercept_rules.json").write_text(
            json.dumps({"rules": [{"name": "bad", "tool_pattern": "("}]}), encoding="utf-8")
        with self.assertRaises(ConfigError):
            audit_config.load_intercept_rules(home)


class InterceptDecideDB(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def _ctx(self, autonomy="full"):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-intercept",
                              checklist_id=None, use_llm=True, autonomy=autonomy)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        ctx.registry["test.read"] = ToolSpec(name="test.read", risk="read", args_schema={}, scope_args={},
                                             executor=lambda a: ToolOutput(b"{}", "t/1"))
        return ctx

    def test_matching_rule_forces_approval_even_for_read_tool_in_full_autonomy(self):
        ctx = self._ctx(autonomy="full")
        ctx.cfg.intercept_rules = [{"name": "test-rule", "tool_re": re.compile("test.read"), "arg_re": None}]
        asset_id = next(iter(ctx.targets))
        d = policy.decide(ctx, "test.read", {}, asset_id)
        self.assertEqual(d.decision, policy.ALLOW_WITH_APPROVAL)
        self.assertIn("test-rule", d.reason)

    def test_no_rules_leaves_read_tool_allowed(self):
        ctx = self._ctx(autonomy="full")
        ctx.cfg.intercept_rules = []
        asset_id = next(iter(ctx.targets))
        d = policy.decide(ctx, "test.read", {}, asset_id)
        self.assertEqual(d.decision, policy.ALLOW)

    def test_arg_pattern_must_also_match(self):
        ctx = self._ctx(autonomy="full")
        ctx.cfg.intercept_rules = [{"name": "secret-rule", "tool_re": re.compile("test.read"),
                                    "arg_re": re.compile("secret")}]
        asset_id = next(iter(ctx.targets))
        self.assertEqual(policy.decide(ctx, "test.read", {"q": "nothing"}, asset_id).decision, policy.ALLOW)
        d = policy.decide(ctx, "test.read", {"q": "my secret"}, asset_id)
        self.assertEqual(d.decision, policy.ALLOW_WITH_APPROVAL)


if __name__ == "__main__":
    unittest.main()
