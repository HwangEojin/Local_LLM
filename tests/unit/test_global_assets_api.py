"""G5-b: 전역 자산 화면 API(/api/assets, /api/assets/{id}, UPGRADE_PLAN.md)를 확인한다.

- 목록: company/kind/q 필터, 자산별 관찰 실행 수·중복 제외 finding 수·최근 관찰 시각.
- 상세: 양방향 자산 관계(asset_edges), 이 자산을 관찰한 실행 이력, 중복 제외 finding 목록.
- 새 진단 로직 없음 — 기존 테이블을 조회만 한다.
"""
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db, runs


class GlobalAssetsApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg = config.load(None)
            cls.conn = db.connect(cls.cfg.dsn)
            db.require_migrated(cls.conn, cls.cfg.migrations_dir)
            from fastapi.testclient import TestClient
            from webapi import deps, main
        except (ConfigError, ImportError) as e:
            raise unittest.SkipTest(f"DB/웹 의존성 불가: {e}") from None
        main.app.dependency_overrides[deps.current_user] = lambda: "pytest-assets"
        cls.client = TestClient(main.app)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "client"):
            from webapi import main
            main.app.dependency_overrides.clear()
            cls.conn.close()

    def _run_and_asset(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-assets",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        asset = self.conn.execute(
            "SELECT a.* FROM run_assets ra JOIN assets a ON a.id = ra.asset_id WHERE ra.run_id=%s",
            (run["id"],)).fetchone()
        return run, asset

    def test_list_returns_the_scoped_asset_with_filters(self):
        run, asset = self._run_and_asset()
        body = self.client.get(f"/api/assets?kind={asset['kind']}&q={asset['value']}&limit=100").json()
        values = {it["value"] for it in body["items"]}
        self.assertIn(asset["value"], values)
        row = next(it for it in body["items"] if it["id"] == str(asset["id"]))
        self.assertGreaterEqual(row["observed_runs"], 1)

    def test_list_kind_filter_excludes_other_kinds(self):
        run, asset = self._run_and_asset()
        body = self.client.get(f"/api/assets?kind=__no_such_kind__&q={asset['value']}&limit=100").json()
        self.assertNotIn(asset["value"], {it["value"] for it in body["items"]})

    def test_detail_includes_run_history_and_dedup_findings(self):
        run, asset = self._run_and_asset()
        fid = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification) "
            "VALUES (%s,%s,'자산 상세 테스트 발견','근거','대응','RULE_CONFIRMED') RETURNING id",
            (run["id"], asset["id"])).fetchone()["id"]
        dup_id = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification, duplicate_of) "
            "VALUES (%s,%s,'중복 발견','근거','대응','RULE_CONFIRMED',%s) RETURNING id",
            (run["id"], asset["id"], fid)).fetchone()["id"]
        self.conn.commit()  # 웹 API 는 별도 연결을 쓰므로(deps.get_conn) 커밋 전에는 안 보임
        body = self.client.get(f"/api/assets/{asset['id']}").json()
        self.assertEqual(body["asset"]["id"], str(asset["id"]))
        self.assertIn(str(run["id"]), {r["id"] for r in body["runs"]})
        titles = {f["title"] for f in body["findings"]}
        self.assertIn("자산 상세 테스트 발견", titles)
        self.assertNotIn("중복 발견", titles)  # duplicate_of 가 있으면 빠져야 함
        self.assertTrue(str(dup_id))  # dup_id 를 만들었다는 사실 자체가 위 단언의 전제

    def test_detail_unknown_asset_is_404(self):
        res = self.client.get("/api/assets/00000000-0000-0000-0000-000000000000")
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
