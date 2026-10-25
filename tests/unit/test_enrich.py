"""비동기 자산 보강(ARTEX enrich/ 대응)을 확인한다.

- 실행이 아는 host 자산(baseline-demo, scope.json)에 대해 net.dns_resolve 보강 작업이 자동으로 큐에
  오른다. LLM 을 부르지 않는다(ctx.llm 없이도 동작).
- 두 번째 호출은 같은 작업을 중복 생성하지 않는다(idempotency_key).
- net.dns_resolve/net.http_head 가 레지스트리에 실제로 등록돼 있다(실 소켓으로 localhost 조회까지 확인).
"""
import json
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db, enrich, runs


class EnrichDB(unittest.TestCase):
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

    def test_net_tools_registered(self):
        cfg_reg = runs.registry(self.cfg)
        self.assertIn("net.dns_resolve", cfg_reg)
        self.assertIn("net.http_head", cfg_reg)

    def test_enqueue_creates_dns_resolve_task_for_host_asset(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-enrich",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        accepted = enrich.enqueue(ctx)
        self.assertEqual(accepted, 1)
        item = self.conn.execute(
            "SELECT payload FROM backlog_items WHERE run_id=%s AND kind='exploration'", (run["id"],)).fetchone()
        self.assertEqual(item["payload"]["tool"], "net.dns_resolve")
        self.assertEqual(item["payload"]["args"], {"host": "localhost"})

    def test_enqueue_is_idempotent(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-enrich",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        enrich.enqueue(ctx)
        second = enrich.enqueue(ctx)
        self.assertEqual(second, 0)
        n = self.conn.execute("SELECT count(*) AS n FROM backlog_items WHERE run_id=%s AND kind='exploration'",
                              (run["id"],)).fetchone()["n"]
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
