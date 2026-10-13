"""diagnostic_workbench 최소 동작 확인. 전용 PostgreSQL DB 가 필요하다(실행 기록이 남는다).

    set WORKBENCH_TEST_PG_DSN=postgresql://user@127.0.0.1:5432/workbench_test
    python -m unittest tests.integration.test_workbench
"""
import os
import unittest

from tests.helpers import Home

DSN = os.environ.get("WORKBENCH_TEST_PG_DSN")


@unittest.skipUnless(DSN, "WORKBENCH_TEST_PG_DSN 이 설정되지 않음")
class WorkbenchSmokeTest(unittest.TestCase):
    def setUp(self):
        from diagnostic_workbench import config, db
        os.environ[config.DSN_ENV] = DSN
        self.home = Home()
        self.cfg = config.load(str(self.home.path))
        self.conn = db.connect(self.cfg.dsn)
        db.migrate(self.conn, self.cfg.migrations_dir)

    def tearDown(self):
        self.conn.close()
        self.home.storage.close()

    def test_run_is_planned_once_executed_and_evidenced(self):
        from audit.models import PolicyDenied
        from diagnostic_workbench import coordinator, evidence, planner, runs
        with self.assertRaises(PolicyDenied):  # 범위 밖 대상은 실행조차 만들 수 없다
            runs.create_run(self.conn, self.cfg, "sample-local-config", "0.1.0", ["not-in-scope"], "test")
        run = runs.create_run(self.conn, self.cfg, "sample-local-config", "0.1.0", ["sample-app"], "test")
        ctx = runs.build_context(self.cfg, self.conn, run)
        self.assertEqual(planner.plan(ctx)["created"], 6)
        self.assertEqual(planner.plan(ctx)["created"], 0)  # 같은 상태에서 다시 계획해도 중복이 없다
        final = coordinator.run_loop(self.cfg, self.conn, run, log=lambda _: None)
        self.assertEqual(final["status"], "COMPLETED")
        q = lambda sql: self.conn.execute(sql, (run["id"],)).fetchall()  # noqa: E731
        verdicts = {r["check_id"]: r["verdict"] for r in q(
            "SELECT ci.check_id, rci.verdict FROM run_checklist_items rci JOIN checklist_items ci "
            "ON ci.id = rci.checklist_item_id WHERE rci.run_id = %s")}
        self.assertEqual((verdicts["SAMPLE-001"], verdicts["SAMPLE-003"], verdicts["SAMPLE-004"]),
                         ("FAIL", "PASS", "NEEDS_REVIEW"))
        self.assertFalse(q("SELECT 1 FROM worker_runs WHERE run_id = %s GROUP BY backlog_item_id HAVING count(*) > 1"))
        rows = q("SELECT * FROM evidence WHERE run_id = %s")
        self.assertTrue(rows)
        self.assertEqual([evidence.verify(e, self.cfg.evidence_dir) for e in rows], [None] * len(rows))
        for r in q("SELECT verdict, evidence_ids FROM verdict_history vh JOIN run_checklist_items rci "
                   "ON rci.id = vh.run_checklist_item_id WHERE rci.run_id = %s AND new_verdict IN ('PASS','FAIL')"):
            self.assertTrue(r["evidence_ids"])  # PASS/FAIL 에는 증적이 연결돼 있어야 한다

    def test_excluded_value_matching(self):
        from diagnostic_workbench.policy import _excluded
        self.assertEqual(_excluded("C:\\app\\conf\\a.txt", ["c:/app/conf"]), "c:/app/conf")
        self.assertEqual(_excluded("https://Admin.example.test/x", ["admin.example.test"]), "admin.example.test")
        self.assertIsNone(_excluded("c:/app/config2/a.txt", ["c:/app/conf"]))
