"""자율성 레벨 이분법(approval/full, 2026-10-19 — 3단계 partial 을 없앰)을 확인한다.

도구 호출 위험도 체계는 삭제됐다(CLAUDE.md §8, 2026-11) — 승인 요구 여부는 이제 인터셉트 규칙(ask) 매치
여부로만 정해진다. 인터셉트 규칙은 운영자가 '이건 항상 사람이 보게 한다'고 등록하는 통제라 autonomy
(approval/full 어느 쪽이든)로 건너뛸 수 없다 — autonomy 는 더 이상 policy.decide() 의 승인 요구 여부에
영향을 주지 않는다(§7 '자율성 확장' 당시의 목적이던 위험도 승인 생략 경로가 없어졌기 때문)."""
import re
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

    def _ctx_with_write_tool(self, autonomy, intercepted=True):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-autonomy",
                              checklist_id=None, use_llm=True, autonomy=autonomy)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        ctx.registry["test.write"] = ToolSpec(name="test.write", risk="write", args_schema={}, scope_args={},
                                              executor=lambda args: ToolOutput(b"{}", "test.write/1"))
        ctx.cfg.intercept_rules = [{"name": "test-rule", "tool_re": re.compile(r"^test\.write$"), "arg_re": None}] \
            if intercepted else []  # 이 PC 의 config/intercept_rules.local.json 내용과 무관하게 결정론적으로 만든다
        asset_id = self.conn.execute(
            "SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        return ctx, asset_id

    def test_no_intercept_rule_allows_without_approval_regardless_of_autonomy(self):
        # 위험도 체계 삭제(CLAUDE.md §8, 2026-11) — 인터셉트 규칙에 안 걸리면 write 도구도 바로 허용된다.
        for autonomy in runs.AUTONOMY_LEVELS:
            ctx, asset_id = self._ctx_with_write_tool(autonomy, intercepted=False)
            d = policy.decide(ctx, "test.write", {}, asset_id)
            self.assertEqual(d.decision, policy.ALLOW, autonomy)

    def test_intercept_rule_requires_approval_regardless_of_autonomy(self):
        # 'full' 도 인터셉트 규칙(사람이 항상 보게 등록한 규칙)을 건너뛰지 못한다(test_intercept_rules.py 와 동일 원칙).
        for autonomy in runs.AUTONOMY_LEVELS:
            ctx, asset_id = self._ctx_with_write_tool(autonomy)
            d = policy.decide(ctx, "test.write", {}, asset_id)
            self.assertEqual(d.decision, policy.ALLOW_WITH_APPROVAL, autonomy)


if __name__ == "__main__":
    unittest.main()
