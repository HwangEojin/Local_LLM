"""막힌 작업 일괄 재시도(POST /api/runs/{run}/backlog/retry-all)를 확인한다.

- FAILED/BLOCKED 작업은 한 번에 PENDING 으로 되돌리되, 승인 대기 중(policy_class='ALLOW_WITH_APPROVAL')인
  BLOCKED 작업은 그대로 둔다 — 사람이 직접 승인/거부해야 하므로 건드리지 않는다(승인 우회 경로 금지).
- 각 작업은 기존 worker.retry_item 을 그대로 거친다(새 재시도 로직 없음).
"""
import unittest

from psycopg.types.json import Jsonb

from audit.models import ConfigError
from diagnostic_workbench import config, db, runs


class RetryAllTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            real = config.load(None)
            conn = db.connect(real.dsn)
            db.require_migrated(conn, real.migrations_dir)
            conn.close()
            from fastapi.testclient import TestClient
            from webapi import deps
            from webapi.main import app
        except (ConfigError, ImportError) as e:
            raise unittest.SkipTest(f"DB/웹 의존성 불가: {e}") from None
        import json
        cls.cfg = real
        if "baseline-demo" not in {t["target_id"] for t in json.loads(
                (real.home / "config" / "scope.json").read_text(encoding="utf-8"))["targets"]}:
            raise unittest.SkipTest("scope.json 에 baseline-demo 대상이 없음")
        cls.deps = deps
        app.dependency_overrides[deps.current_user] = lambda: "pytest-retry-all"
        cls.app, cls.client = app, TestClient(app)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "app"):
            cls.app.dependency_overrides.clear()

    def setUp(self):
        self.conn = db.connect(self.cfg.dsn)
        self.addCleanup(self.conn.close)

    def _run(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-retry-all", use_llm=True)
        self.addCleanup(self.conn.commit)
        return run

    def _item(self, run, status, policy_class=None):
        asset_id = self.conn.execute("SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        node = self.conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'intent','test','{}') "
            "RETURNING id", (run["id"],)).fetchone()["id"]
        return self.conn.execute(
            "INSERT INTO backlog_items (run_id, kind, exploration_node_id, asset_id, status, idempotency_key, "
            "reason, payload, policy_class) VALUES (%s,'exploration',%s,%s,%s,%s,'테스트',%s,%s) RETURNING id",
            (run["id"], node, asset_id, status, f"retry-all-{node}", Jsonb({"tool": "fs.list_dir", "args": {}}),
             policy_class)).fetchone()["id"]

    def test_retries_failed_and_blocked_but_not_approval_pending(self):
        run = self._run()
        failed = self._item(run, "FAILED")
        blocked = self._item(run, "BLOCKED", policy_class="DENY")
        pending_approval = self._item(run, "BLOCKED", policy_class="ALLOW_WITH_APPROVAL")
        self.conn.commit()  # 웹 API 는 다른 커넥션을 쓰므로 커밋해야 보임

        r = self.client.post(f"/api/runs/{run['run_code']}/backlog/retry-all", json={"reason": ""})
        self.assertEqual(r.status_code, 422)  # 사유 필수

        r = self.client.post(f"/api/runs/{run['run_code']}/backlog/retry-all", json={"reason": "일괄 재시도 테스트"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sorted(r.json()["retried"]), sorted([str(failed), str(blocked)]))
        self.assertEqual(r.json()["count"], 2)

        statuses = {row["id"]: row["status"] for row in self.conn.execute(
            "SELECT id, status FROM backlog_items WHERE id = ANY(%s)", ([failed, blocked, pending_approval],))}
        self.assertEqual(statuses[failed], "PENDING")
        self.assertEqual(statuses[blocked], "PENDING")
        self.assertEqual(statuses[pending_approval], "BLOCKED")  # 승인 대기는 그대로

    def test_retry_all_is_scoped_to_its_own_run(self):
        run_a, run_b = self._run(), self._run()
        item_a = self._item(run_a, "FAILED")
        item_b = self._item(run_b, "FAILED")
        self.conn.commit()  # 웹 API 는 다른 커넥션을 쓰므로 커밋해야 보임
        r = self.client.post(f"/api/runs/{run_a['run_code']}/backlog/retry-all", json={"reason": "범위 테스트"})
        self.assertEqual(r.json()["count"], 1)
        status_a = self.conn.execute("SELECT status FROM backlog_items WHERE id=%s", (item_a,)).fetchone()["status"]
        status_b = self.conn.execute("SELECT status FROM backlog_items WHERE id=%s", (item_b,)).fetchone()["status"]
        self.assertEqual(status_a, "PENDING")  # 대상 실행의 작업은 재시도됨
        self.assertEqual(status_b, "FAILED")  # 다른 실행의 작업은 안 건드림


if __name__ == "__main__":
    unittest.main()
