"""자산 멀티테넌시(ARTEX companies/company_scope 대응, 전체판, assets.company)를 확인한다.

0014 는 company 를 보고용 태그로만 썼다(assets 는 (kind, value) 전역 유니크라 회사가 달라도 같은
호스트명은 한 자산을 공유). 0015 부터는 (company, kind, value) 가 유니크 키라, 같은 값이라도 회사가
다르면 실제로 다른 자산 행이 된다 — 한 회사의 발견 사항/증적이 다른 회사의 자산에 섞이지 않는다.
company 를 안 쓰면(기존 scope.json 전부) ''(빈 문자열)로 저장돼 기존 단일 회사 사용에는 영향이 없다.
"""
import json
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db, runs


class AssetCompanyIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg = config.load(None)
            cls.conn = db.connect(cls.cfg.dsn)
            db.require_migrated(cls.conn, cls.cfg.migrations_dir)
        except ConfigError as e:
            raise unittest.SkipTest(f"DB 연결/마이그레이션 불가: {e}") from None
        cls.scope_path = cls.cfg.home / "config" / "scope.json"
        cls._backup = cls.scope_path.read_text(encoding="utf-8")
        scope = json.loads(cls._backup)
        base = next((t for t in scope["targets"] if t["target_id"] == "baseline-demo"), None)
        if base is None:
            raise unittest.SkipTest("scope.json 에 baseline-demo 대상이 없음")
        base["company"] = "acme-corp"
        # 같은 호스트명(value)을 다른 회사(globex-corp)로도 선언해, 격리가 실제로 자산을 나누는지 본다.
        tenant_b = {**base, "target_id": "baseline-demo-tenant-b", "company": "globex-corp"}
        scope["targets"].append(tenant_b)
        cls.scope_path.write_text(json.dumps(scope, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.scope_path.write_text(cls._backup, encoding="utf-8")
        if hasattr(cls, "conn"):
            cls.conn.close()

    def test_declared_company_tags_the_asset(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-company-tag",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        asset = self.conn.execute(
            "SELECT a.company FROM assets a JOIN run_assets ra ON ra.asset_id = a.id WHERE ra.run_id=%s",
            (run["id"],)).fetchone()
        self.assertEqual(asset["company"], "acme-corp")

    def test_same_host_different_company_are_separate_assets(self):
        run_a = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-tenant-a",
                                checklist_id=None, use_llm=True)
        run_b = runs.create_run(self.conn, self.cfg, ["baseline-demo-tenant-b"], "pytest-tenant-b",
                                checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        asset_a = self.conn.execute(
            "SELECT a.id, a.company FROM assets a JOIN run_assets ra ON ra.asset_id = a.id WHERE ra.run_id=%s",
            (run_a["id"],)).fetchone()
        asset_b = self.conn.execute(
            "SELECT a.id, a.company FROM assets a JOIN run_assets ra ON ra.asset_id = a.id WHERE ra.run_id=%s",
            (run_b["id"],)).fetchone()
        self.assertNotEqual(asset_a["id"], asset_b["id"])
        self.assertEqual(asset_a["company"], "acme-corp")
        self.assertEqual(asset_b["company"], "globex-corp")


if __name__ == "__main__":
    unittest.main()
