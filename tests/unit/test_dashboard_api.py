"""대시보드 백엔드(src/webapi/routers/dashboard.py)를 확인한다 — 특히 B3(토큰 사용량) 추가분.
새 진단 로직 없음(runs/findings/evidence/model_calls 집계만) — 실제 PostgreSQL 로 확인."""
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db


class DashboardApiTest(unittest.TestCase):
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
        app.dependency_overrides[deps.current_user] = lambda: "pytest-dashboard"
        cls.app, cls.client = app, TestClient(app)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "app"):
            cls.app.dependency_overrides.clear()

    def test_shape_includes_token_usage(self):
        out = self.client.get("/api/dashboard").json()
        for key in ("by_status", "by_severity", "totals", "recent_runs", "token_daily", "token_totals"):
            self.assertIn(key, out)
        self.assertIn("calls", out["token_totals"])
        self.assertIn("tokens", out["token_totals"])
        if out["token_daily"]:
            self.assertIn("day", out["token_daily"][0])
            self.assertIn("tokens", out["token_daily"][0])


if __name__ == "__main__":
    unittest.main()
