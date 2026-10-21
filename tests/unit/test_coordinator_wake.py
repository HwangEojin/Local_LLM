"""깨어나는 조건(이벤트+디바운스+하트비트, CLAUDE.md §5.4, 2026-10-19)을 확인한다.

실제 하트비트(600s)/디바운스(800ms)를 그대로 기다리면 테스트가 느려지므로, 모듈 상수를 테스트 동안만
짧게 바꿔서 확인한다(둘 다 평범한 전역 변수라 함수 호출 시점에 다시 읽힘).
"""
import threading
import time
import unittest

from audit.models import ConfigError
from diagnostic_workbench import coordinator, db, config, db as dbmod, runs


class WakeConditionTest(unittest.TestCase):
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

    def setUp(self):
        self._orig = (coordinator._HEARTBEAT_S, coordinator._DEBOUNCE_S, coordinator._POLL_S)
        coordinator._HEARTBEAT_S, coordinator._DEBOUNCE_S, coordinator._POLL_S = 0.3, 0.1, 0.03

    def tearDown(self):
        coordinator._HEARTBEAT_S, coordinator._DEBOUNCE_S, coordinator._POLL_S = self._orig

    def _ctx_no_llm(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-wake",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        ctx.llm = None  # planner.plan() 의 탐색 분기를 건너뛰어 LLM 호출 없이 빠르게 테스트한다
        return ctx, run

    def test_heartbeat_returns_no_work_when_nothing_changes(self):
        ctx, run = self._ctx_no_llm()
        name = "pytest-worker"
        deadline = time.monotonic() + 10
        start = time.monotonic()
        item, reason = coordinator._wait_for_wake(ctx, self.conn, run["id"], self.cfg, name, deadline, lambda *a, **k: None)
        elapsed = time.monotonic() - start
        self.assertIsNone(item)
        self.assertEqual(reason, "NO_WORK")
        self.assertLess(elapsed, 2)  # 하트비트(0.3s)보다 조금만 더 걸려야 함(600s 를 기다리지 않음)

    def test_stop_file_detected_during_wait(self):
        ctx, run = self._ctx_no_llm()
        name = "pytest-worker"
        deadline = time.monotonic() + 10

        def trigger():
            time.sleep(0.15)
            self.cfg.stop_file.parent.mkdir(parents=True, exist_ok=True)
            self.cfg.stop_file.touch()
        threading.Thread(target=trigger, daemon=True).start()
        try:
            item, reason = coordinator._wait_for_wake(ctx, self.conn, run["id"], self.cfg, name, deadline, lambda *a, **k: None)
        finally:
            self.cfg.stop_file.unlink(missing_ok=True)
        self.assertIsNone(item)
        self.assertIn("전체 중지 스위치", reason)

    def test_event_wakes_before_heartbeat_and_claims_new_work(self):
        # 스레드로 다른 커넥션을 또 여는 대신, 이벤트와 작업을 먼저 넣어 둔 뒤 기다리게 한다 — 운영
        # 코드는 늘 커넥션 하나를 한 스레드에서만 쓰므로, 테스트도 같은 커넥션을 여러 스레드에서 동시에
        # 쓰지 않는다(psycopg 커넥션은 스레드 간 동시 사용을 보장하지 않음).
        ctx, run = self._ctx_no_llm()
        name = "pytest-worker"
        asset_id = self.conn.execute(
            "SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        node = self.conn.execute(
            "SELECT id FROM exploration_nodes WHERE run_id=%s AND kind='goal' LIMIT 1",
            (run["id"],)).fetchone()["id"]
        self.conn.execute(
            "INSERT INTO backlog_items (run_id, kind, exploration_node_id, asset_id, priority, "
            "idempotency_key, reason, payload, status, max_attempts) VALUES "
            "(%s,'exploration',%s,%s,80,'pytest-wake-item','테스트 작업','{}','READY',1)",
            (run["id"], node, asset_id))
        dbmod.notify_change(self.conn, run["id"], "fact")
        self.conn.commit()
        deadline = time.monotonic() + 10
        start = time.monotonic()
        item, reason = coordinator._wait_for_wake(ctx, self.conn, run["id"], self.cfg, name, deadline, lambda *a, **k: None)
        elapsed = time.monotonic() - start
        self.assertIsNotNone(item)
        self.assertIsNone(reason)
        self.assertEqual(item["idempotency_key"], "pytest-wake-item")
        self.assertLess(elapsed, 1)  # 디바운스(0.1s) 수준으로 빠르게 깨어나야 함(하트비트까지 기다리지 않음)


if __name__ == "__main__":
    unittest.main()
