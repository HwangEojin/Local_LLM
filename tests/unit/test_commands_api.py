"""명령 이력 화면의 백엔드(src/webapi/routers/commands.py)를 확인한다.

- /api/commands 가 run_code 를 포함해 도구 이름/상태로 거르고 페이지네이션한다.
- /api/commands/stats 가 도구별 호출·실패 집계를 돌려준다.
- 새 진단 로직 없음(traces.search_all_worker_traces 조회만) — 실제 PostgreSQL 로 확인.
"""
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db


class CommandsApiTest(unittest.TestCase):
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
        cls.deps = deps
        app.dependency_overrides[deps.current_user] = lambda: "pytest-commands"
        cls.app, cls.client = app, TestClient(app)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "app"):
            cls.app.dependency_overrides.clear()

    def test_list_shape_and_pagination(self):
        out = self.client.get("/api/commands", params={"limit": 2}).json()
        self.assertIn("items", out)
        self.assertIn("total", out)
        self.assertLessEqual(len(out["items"]), 2)
        if out["items"]:
            self.assertIn("run_code", out["items"][0])

    def test_filters_narrow_results(self):
        stats = self.client.get("/api/commands/stats").json()
        if not stats:
            self.skipTest("tool_invocations 데이터 없음")
        tool = stats[0]["tool"]
        out = self.client.get("/api/commands", params={"tool": tool, "limit": 200}).json()
        self.assertTrue(out["items"])
        self.assertTrue(all(i["tool"] == tool for i in out["items"]))
        self.assertEqual(out["total"], stats[0]["calls"])

    def test_limit_is_clamped(self):
        out = self.client.get("/api/commands", params={"limit": 10000}).json()
        self.assertEqual(out["limit"], 200)


if __name__ == "__main__":
    unittest.main()
