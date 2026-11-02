"""전역 발견 사항/트래픽 화면의 백엔드(src/webapi/routers/global_views.py, UPGRADE_PLAN.md B2)를 확인한다.

- /api/findings, /api/traffic 이 run_code 를 포함해 전체 진단 범위로 페이지네이션한다.
- 새 진단 로직 없음(findings/evidence 를 runs 와 조인해 조회만) — 실제 PostgreSQL 로 확인.
"""
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db


class GlobalViewsApiTest(unittest.TestCase):
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
        app.dependency_overrides[deps.current_user] = lambda: "pytest-global-views"
        cls.app, cls.client = app, TestClient(app)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "app"):
            cls.app.dependency_overrides.clear()

    def test_findings_list_shape_and_pagination(self):
        out = self.client.get("/api/findings", params={"limit": 2}).json()
        self.assertIn("items", out)
        self.assertIn("total", out)
        self.assertLessEqual(len(out["items"]), 2)
        if out["items"]:
            self.assertIn("run_code", out["items"][0])
            self.assertIn("title", out["items"][0])

    def test_findings_status_filter_narrows_results(self):
        all_out = self.client.get("/api/findings", params={"limit": 500}).json()
        statuses = {f["status"] for f in all_out["items"]}
        if not statuses:
            self.skipTest("findings 테이블이 비어 있음")
        pick = next(iter(statuses))
        filtered = self.client.get("/api/findings", params={"status": pick, "limit": 500}).json()
        self.assertTrue(filtered["items"])
        self.assertTrue(all(f["status"] == pick for f in filtered["items"]))

    def test_traffic_list_shape_and_pagination(self):
        out = self.client.get("/api/traffic", params={"limit": 2}).json()
        self.assertIn("items", out)
        self.assertIn("total", out)
        self.assertLessEqual(len(out["items"]), 2)
        if out["items"]:
            self.assertIn("run_code", out["items"][0])
            self.assertIn(out["items"][0]["tool"], ("proxy.http", "proxy.https"))


if __name__ == "__main__":
    unittest.main()
