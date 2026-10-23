"""진단 소프트 삭제(migrations/0011, runs.soft_delete_run, /api/runs/*/delete|restore|bulk-delete)를 확인한다.

- 삭제는 행을 지우지 않고 표시만 한다: 기본 목록에서 숨겨지고, 증적·감사 로그는 남고, 복원할 수 있다.
- 실행 중인 루프가 있는 진단은 삭제를 거부하고, 삭제된 진단은 시작할 수 없다.
- 삭제/복원은 사유와 함께 audit_events 에 남는다(추가 전용이라 이 테스트의 기록도 남는다).
- 이 테스트가 만든 진단은 끝에서 스스로 삭제 표시해 목록을 어지럽히지 않는다.
"""
import unittest

from audit.models import AuditError, ConfigError
from diagnostic_workbench import config, coordinator, db, runs


class RunSoftDeleteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg = config.load(None)
            cls.conn = db.connect(cls.cfg.dsn)
            db.require_migrated(cls.conn, cls.cfg.migrations_dir)
            from fastapi.testclient import TestClient
            from webapi import deps, main
            from webapi.routers import runs as runs_router
        except (ConfigError, ImportError) as e:
            raise unittest.SkipTest(f"DB/웹 의존성 불가: {e}") from None
        main.app.dependency_overrides[deps.current_user] = lambda: "pytest-delete"
        cls.app, cls.client, cls.router = main.app, TestClient(main.app), runs_router
        cls.created = []

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "app"):
            cls.app.dependency_overrides.clear()
            for rid in cls.created:  # 이 테스트가 만든 진단은 목록에서 치운다
                row = cls.conn.execute("SELECT * FROM runs WHERE id=%s", (rid,)).fetchone()
                if row and not row["deleted_at"]:
                    runs.soft_delete_run(cls.conn, row, "pytest-cleanup", "테스트 정리")
            cls.conn.close()

    def _run(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-run-delete", use_llm=True)
        self.created.append(run["id"])
        return run

    def _codes(self, deleted="exclude"):
        # 이 테스트가 만든 진단의 운영자가 pytest-* 라 기본(tests=hide)으로는 목록에 안 보인다 — 포함해서 본다
        r = self.client.get(f"/api/runs?q=pytest-run-delete&limit=100&deleted={deleted}&tests=show")
        return {x["run_code"] for x in r.json()["runs"]}

    def test_test_runs_are_hidden_by_default(self):
        code = self._run()["run_code"]  # 운영자 pytest-run-delete
        default = self.client.get("/api/runs?q=pytest-run-delete&limit=100").json()
        self.assertNotIn(code, {x["run_code"] for x in default["runs"]})
        shown = self.client.get("/api/runs?q=pytest-run-delete&limit=100&tests=show").json()
        self.assertIn(code, {x["run_code"] for x in shown["runs"]})
        only = self.client.get("/api/runs?limit=100&tests=only").json()
        self.assertTrue(only["runs"] and all(x["operator"].lower().startswith("pytest") for x in only["runs"]))
        # 사람이 만든 진단(운영자가 pytest 로 시작하지 않음)은 기본 목록에 그대로 보인다
        real = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "hong", use_llm=True)
        self.created.append(real["id"])
        default = self.client.get("/api/runs?q=hong&limit=100").json()
        self.assertIn(real["run_code"], {x["run_code"] for x in default["runs"]})

    def test_delete_hides_from_default_list_and_restore_brings_back(self):
        run = self._run()
        code = run["run_code"]
        self.assertIn(code, self._codes())
        r = self.client.post(f"/api/runs/{code}/delete", json={"reason": "테스트", "confirm": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn(code, self._codes())
        self.assertIn(code, self._codes("only"))
        self.assertIn(code, self._codes("all"))
        # 행·감사 기록은 남아 있고 직접 조회도 된다(삭제 표시 포함)
        detail = self.client.get(f"/api/runs/{code}").json()["run"]
        self.assertTrue(detail["deleted_at"])
        self.assertEqual(detail["delete_reason"], "테스트")
        ev = self.conn.execute("SELECT details FROM audit_events WHERE run_id=%s AND event='run_deleted'",
                               (run["id"],)).fetchone()
        self.assertEqual(ev["details"]["actor"], "web:pytest-delete")
        r = self.client.post(f"/api/runs/{code}/restore", json={"reason": "되돌림"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn(code, self._codes())
        self.assertTrue(self.conn.execute("SELECT 1 FROM audit_events WHERE run_id=%s AND event='run_restored'",
                                          (run["id"],)).fetchone())

    def test_delete_requires_confirm_and_reason(self):
        code = self._run()["run_code"]
        self.assertEqual(self.client.post(f"/api/runs/{code}/delete", json={"reason": "x"}).status_code, 400)
        r = self.client.post(f"/api/runs/{code}/delete", json={"reason": "  ", "confirm": True})
        self.assertEqual(r.status_code, 400)  # 사유 없음
        self.assertIn(code, self._codes())

    def test_running_run_cannot_be_deleted(self):
        run = self._run()
        self.router._RUNNING_LOOPS[str(run["id"])] = object()
        self.addCleanup(self.router._RUNNING_LOOPS.pop, str(run["id"]), None)
        r = self.client.post(f"/api/runs/{run['run_code']}/delete", json={"reason": "x", "confirm": True})
        self.assertEqual(r.status_code, 409)
        self.assertIn(run["run_code"], self._codes())

    def test_deleted_run_cannot_start(self):
        run = self._run()
        runs.soft_delete_run(self.conn, run, "pytest", "시작 차단 확인")
        deleted = db.resolve_run(self.conn, run["run_code"])
        r = self.client.post(f"/api/runs/{run['run_code']}/start")
        self.assertEqual(r.status_code, 400)
        with self.assertRaises(AuditError):
            coordinator.run_loop(self.cfg, self.conn, deleted)
        with self.assertRaises(ConfigError):  # 이미 삭제된 것을 다시 삭제할 수 없다
            runs.soft_delete_run(self.conn, deleted, "pytest", "중복")

    def test_bulk_delete_reports_skipped(self):
        a, b, running = self._run(), self._run(), self._run()
        self.router._RUNNING_LOOPS[str(running["id"])] = object()
        self.addCleanup(self.router._RUNNING_LOOPS.pop, str(running["id"]), None)
        refs = [a["run_code"], b["run_code"], running["run_code"], "NOPE-00000000-00"]
        r = self.client.post("/api/runs/bulk-delete", json={"refs": refs, "reason": "일괄", "confirm": True})
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertEqual(sorted(out["deleted"]), sorted([a["run_code"], b["run_code"]]))
        self.assertEqual({s["ref"] for s in out["skipped"]}, {running["run_code"], "NOPE-00000000-00"})
        self.assertEqual(self.client.post("/api/runs/bulk-delete",
                                          json={"refs": [], "reason": "x", "confirm": True}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
