"""체크리스트·탐색 도구 호출 예산 분리(CLAUDE.md §2): worker._tool_calls_used 가 kind 별로 따로 세는지를
실제 PostgreSQL 로 확인한다. evidence/audit_events 와 달리 tool_invocations 는 추가 전용이 아니라
DELETE 가 허용되므로 보통 방식대로 정리한다.
"""
import types
import unittest

from psycopg.types.json import Jsonb

from audit.models import ConfigError
from diagnostic_workbench import config, db, worker


class ToolCallsUsedByKind(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cfg = config.load(None)
            cls.conn = db.connect(cfg.dsn)
            db.require_migrated(cls.conn, cfg.migrations_dir)
        except ConfigError as e:
            raise unittest.SkipTest(f"DB 연결/마이그레이션 불가: {e}") from None
        cv = cls.conn.execute("SELECT id FROM checklist_versions LIMIT 1").fetchone()
        if cv is None:
            cls.conn.rollback()
            raise unittest.SkipTest("checklist_versions 에 행이 없음")
        cls.run_id = cls.conn.execute(
            "INSERT INTO runs (engagement_id, operator, checklist_version_id, scope, budget) "
            "VALUES ('TEST-BUDGET-SEPARATION', 'pytest', %s, %s, %s) RETURNING id",
            (cv["id"], Jsonb({}), Jsonb({}))).fetchone()["id"]
        cls.asset = cls.conn.execute(
            "INSERT INTO assets (kind, value, source) VALUES ('host', 'budget-separation-test.invalid', "
            "'operator') ON CONFLICT (kind, value) DO UPDATE SET value = EXCLUDED.value RETURNING id"
        ).fetchone()["id"]
        # backlog_items 는 kind 에 맞는 참조(run_checklist_item_id / exploration_node_id)가 있어야 한다.
        ci = cls.conn.execute("SELECT id FROM checklist_items LIMIT 1").fetchone()
        cls.rci = cls.conn.execute(
            "INSERT INTO run_checklist_items (run_id, checklist_item_id, asset_id) VALUES (%s,%s,%s) "
            "RETURNING id", (cls.run_id, ci["id"], cls.asset)).fetchone()["id"]
        cls.enode = cls.conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'intent','worker','{}') "
            "RETURNING id", (cls.run_id,)).fetchone()["id"]

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.rollback()  # tool_invocations/backlog_items 는 삭제 가능하지만 여기도 그냥 통일해서 격리
            cls.conn.close()

    def _backlog_item(self, kind):
        rci_id = self.rci if kind == "checklist" else None
        enode_id = self.enode if kind == "exploration" else None
        return self.conn.execute(
            "INSERT INTO backlog_items (run_id, kind, run_checklist_item_id, exploration_node_id, asset_id, "
            "idempotency_key, reason) VALUES (%s,%s,%s,%s,%s,%s,'test') RETURNING id",
            (self.run_id, kind, rci_id, enode_id, self.asset, f"test:{kind}:{id(object())}")).fetchone()["id"]

    def _invocation(self, backlog_item_id):
        self.conn.execute(
            "INSERT INTO tool_invocations (run_id, backlog_item_id, asset_id, step_no, tool, args, "
            "policy_decision, status) VALUES (%s,%s,%s,1,'test.tool','{}','ALLOW','SUCCEEDED')",
            (self.run_id, backlog_item_id, self.asset))

    def test_counts_are_independent_per_kind(self):
        ctx = types.SimpleNamespace(conn=self.conn, run={"id": self.run_id})
        checklist_item = self._backlog_item("checklist")
        for _ in range(3):
            self._invocation(checklist_item)
        self.assertEqual(worker._tool_calls_used(ctx, "checklist"), 3)
        self.assertEqual(worker._tool_calls_used(ctx, "exploration"), 0)

        exploration_item = self._backlog_item("exploration")
        self._invocation(exploration_item)
        # 탐색 쪽 호출이 하나 생겼어도 체크리스트 쪽 집계는 그대로다 — 서로 예산을 갉아먹지 않는다.
        self.assertEqual(worker._tool_calls_used(ctx, "checklist"), 3)
        self.assertEqual(worker._tool_calls_used(ctx, "exploration"), 1)

    def test_budget_key_mapping_matches_kind(self):
        # CLAUDE.md §1/§5.4/§8: 자율 탐색(exploration)에는 하드 리밋을 두지 않는다 — 체크리스트만 예산 키가 있다.
        self.assertEqual(worker._BUDGET_KEY["checklist"], "max_tool_calls")
        self.assertNotIn("exploration", worker._BUDGET_KEY)


if __name__ == "__main__":
    unittest.main()
