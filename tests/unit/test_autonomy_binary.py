"""자율성 레벨 이분법(approval/full, 2026-10-19 — 3단계 partial 을 없앰)을 확인한다."""
import unittest

from audit.models import ConfigError, ToolOutput, ToolSpec
from diagnostic_workbench import config, db, policy, runs


class AutonomyBinaryTest(unittest.TestCase):
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

    def test_partial_level_no_longer_accepted(self):
        self.assertEqual(runs.AUTONOMY_LEVELS, ("approval", "full"))
        with self.assertRaises(ConfigError):
            runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-autonomy",
                            checklist_id=None, use_llm=True, autonomy="partial")

    def _ctx_with_write_tool(self, autonomy):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-autonomy",
                              checklist_id=None, use_llm=True, autonomy=autonomy)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        ctx.registry["test.write"] = ToolSpec(name="test.write", risk="write", args_schema={}, scope_args={},
                                              executor=lambda args: ToolOutput(b"{}", "test.write/1"))
        asset_id = self.conn.execute(
            "SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        return ctx, asset_id

    def test_approval_mode_still_requires_approval_for_write(self):
        ctx, asset_id = self._ctx_with_write_tool("approval")
        d = policy.decide(ctx, "test.write", {}, asset_id)
        self.assertEqual(d.decision, policy.ALLOW_WITH_APPROVAL)

    def test_full_mode_auto_allows_write_without_approval(self):
        ctx, asset_id = self._ctx_with_write_tool("full")
        d = policy.decide(ctx, "test.write", {}, asset_id)
        self.assertEqual(d.decision, policy.ALLOW)


if __name__ == "__main__":
    unittest.main()
