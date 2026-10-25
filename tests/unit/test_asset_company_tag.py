"""자산 회사 태그(ARTEX companies 대응 축소판, assets.company)를 확인한다.

scope.json 의 target 에 선택적 company 를 붙이면 그 자산에 태그가 남는다. 없으면(기존 scope.json
전부) company 는 NULL — 동작 변경 없음. 전체 격리는 아니라는 한계를 그대로 테스트로도 지킨다(태그일 뿐).
"""
import json
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db, runs


class AssetCompanyTag(unittest.TestCase):
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
        if "baseline-demo" not in {t["target_id"] for t in scope["targets"]}:
            raise unittest.SkipTest("scope.json 에 baseline-demo 대상이 없음")
        for t in scope["targets"]:
            if t["target_id"] == "baseline-demo":
                t["company"] = "acme-corp"
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


if __name__ == "__main__":
    unittest.main()
