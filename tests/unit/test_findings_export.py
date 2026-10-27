"""발견 사항 내보내기(GET /api/runs/{run}/findings/export)를 확인한다. 새 진단 로직 없음(findings 테이블
그대로 읽어 CSV/JSON 모양만 바꿈) — 실제 PostgreSQL 로 확인."""
import csv
import io
import json
import unittest

from psycopg.types.json import Jsonb

from audit.models import ConfigError
from diagnostic_workbench import config, db, runs


class FindingsExportTest(unittest.TestCase):
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
        import json as _json
        cls.cfg = real
        if "baseline-demo" not in {t["target_id"] for t in _json.loads(
                (real.home / "config" / "scope.json").read_text(encoding="utf-8"))["targets"]}:
            raise unittest.SkipTest("scope.json 에 baseline-demo 대상이 없음")
        cls.deps = deps
        app.dependency_overrides[deps.current_user] = lambda: "pytest-findings-export"
        cls.app, cls.client = app, TestClient(app)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "app"):
            cls.app.dependency_overrides.clear()

    def setUp(self):
        self.conn = db.connect(self.cfg.dsn)
        self.addCleanup(self.conn.close)
        self.run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-findings-export", use_llm=True)
        asset_id = self.conn.execute("SELECT asset_id FROM run_assets WHERE run_id=%s", (self.run["id"],)).fetchone()["asset_id"]
        self.asset_id = asset_id
        for title, status in (("SQLi 테스트", "CONFIRMED"), ("오탐", "REJECTED")):
            self.conn.execute(
                "INSERT INTO findings (run_id, asset_id, title, severity, status, rationale, remediation, "
                "verification) VALUES (%s,%s,%s,'high',%s,'근거','조치','LLM_CONFIRMED')",
                (self.run["id"], asset_id, title, status))
        self.conn.commit()

    def test_csv_export_contains_all_statuses_by_default(self):
        r = self.client.get(f"/api/runs/{self.run['run_code']}/findings/export")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers["content-type"])
        self.assertIn("filename=", r.headers["content-disposition"])
        rows = list(csv.reader(io.StringIO(r.text)))
        titles = {row[0] for row in rows[1:]}
        self.assertEqual(titles, {"SQLi 테스트", "오탐"})  # CSV 는 REJECTED 도 감사용으로 포함

    def test_json_export_and_status_filter(self):
        r = self.client.get(f"/api/runs/{self.run['run_code']}/findings/export", params={"format": "json", "status": "CONFIRMED"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("application/json", r.headers["content-type"])
        rows = json.loads(r.text)
        self.assertEqual([row["title"] for row in rows], ["SQLi 테스트"])
        self.assertEqual(rows[0]["remediation"], "조치")

    def test_bad_format_rejected(self):
        r = self.client.get(f"/api/runs/{self.run['run_code']}/findings/export", params={"format": "xml"})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
