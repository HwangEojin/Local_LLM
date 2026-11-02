"""Planner 가 같은 (자산, 도구) 조합의 연속 차단을 보고 더는 제안하지 않게 하는 worker.blocked_streak()
(CLAUDE.md §6 "동일한 실패를 무한 반복하지 않는다", 2026-11)을 실제 PostgreSQL 로 확인한다."""
import unittest

from psycopg.types.json import Jsonb

from audit.models import ConfigError
from diagnostic_workbench import config, db, worker


class BlockedStreakTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cfg = config.load(None)
            cls.conn = db.connect(cfg.dsn)
            db.require_migrated(cls.conn, cfg.migrations_dir)
        except ConfigError as e:
            raise unittest.SkipTest(f"DB 연결/마이그레이션 불가: {e}") from None
        cls.run_id = cls.conn.execute(
            "INSERT INTO runs (engagement_id, operator, scope, budget) VALUES "
            "('TEST-BLOCKED-STREAK', 'pytest', %s, %s) RETURNING id", (Jsonb({}), Jsonb({}))).fetchone()["id"]
        cls.asset = cls.conn.execute(
            "INSERT INTO assets (kind, value, source) VALUES ('host', 'blocked-streak-test.invalid', 'operator') "
            "ON CONFLICT (company, kind, value) DO UPDATE SET value = EXCLUDED.value RETURNING id").fetchone()["id"]
        cls.enode = cls.conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'intent','worker','{}') "
            "RETURNING id", (cls.run_id,)).fetchone()["id"]
        cls.item = cls.conn.execute(
            "INSERT INTO backlog_items (run_id, kind, exploration_node_id, asset_id, idempotency_key, reason) "
            "VALUES (%s,'exploration',%s,%s,'test:blocked-streak','test') RETURNING id",
            (cls.run_id, cls.enode, cls.asset)).fetchone()["id"]

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.rollback()
            cls.conn.close()

    def _invocation(self, tool, outcome_class, started_at):
        self.conn.execute(
            "INSERT INTO tool_invocations (run_id, backlog_item_id, asset_id, step_no, tool, args, "
            "policy_decision, status, outcome_class, started_at) VALUES (%s,%s,%s,1,%s,'{}','ALLOW','SUCCEEDED',"
            "%s,%s)", (self.run_id, self.item, self.asset, tool, outcome_class, started_at))

    def test_no_invocations_means_zero_streak(self):
        self.assertEqual(worker.blocked_streak(self.conn, self.run_id, self.asset, "streak.unused"), 0)

    def test_counts_consecutive_blocked_most_recent_first(self):
        self._invocation("streak.tool", "blocked", "2026-01-01 00:00:00+00")
        self._invocation("streak.tool", "blocked", "2026-01-01 00:01:00+00")
        self.assertEqual(worker.blocked_streak(self.conn, self.run_id, self.asset, "streak.tool"), 2)

    def test_streak_breaks_on_non_blocked_outcome(self):
        self._invocation("streak.tool2", "blocked", "2026-01-01 00:00:00+00")
        self._invocation("streak.tool2", "ok", "2026-01-01 00:01:00+00")  # 가장 최근
        self.assertEqual(worker.blocked_streak(self.conn, self.run_id, self.asset, "streak.tool2"), 0)

    def test_limit_reached_blocks_further_proposals(self):
        for i in range(worker.BLOCKED_STREAK_LIMIT):
            self._invocation("streak.tool3", "blocked", f"2026-01-01 00:0{i}:00+00")
        self.assertGreaterEqual(worker.blocked_streak(self.conn, self.run_id, self.asset, "streak.tool3"),
                                worker.BLOCKED_STREAK_LIMIT)


if __name__ == "__main__":
    unittest.main()
