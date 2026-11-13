"""D7: finding 심각도(severity) 폐지를 확인한다(UPGRADE_PLAN.md §G-S).

- 새 finding 3경로(LLM 직접 확정, 탐색 기반 RULE_CONFIRMED, DB 직접 INSERT로 시뮬레이션한 체크리스트 FAIL)
  모두 severity 를 쓰지 않고, 컬럼은 NULL 허용으로 남아 기존 값은 보존된다.
- LLM 응답 스키마(explore._FINDING_ITEM)는 여전히 additionalProperties=False 라 severity 필드가 섞여 와도
  거부된다(LLM이 심각도를 주장해도 받아들이지 않음).
- notify.json 에 과거 min_severity 가 남아 있어도 스키마 검증은 통과한다(doctor 가 경고만 함).
"""
import unittest

from audit import schemas
from audit.models import ConfigError
from diagnostic_workbench import config, db, explore, runs, worker


class FindingItemSchemaRejectsSeverity(unittest.TestCase):
    def test_severity_field_is_rejected(self):
        payload = {"target_id": "x", "title": "t", "severity": "high",
                  "rationale": "r", "remediation": "m", "evidence_ids": ["00000000-0000-0000-0000-000000000000"]}
        errs = schemas.validate(payload, explore._FINDING_ITEM)
        self.assertTrue(errs, "severity 필드가 있어도 받아들여짐 — additionalProperties:False 가 깨짐")

    def test_without_severity_is_accepted(self):
        payload = {"target_id": "x", "title": "t",
                  "rationale": "r", "remediation": "m", "evidence_ids": ["00000000-0000-0000-0000-000000000000"]}
        self.assertEqual(schemas.validate(payload, explore._FINDING_ITEM), [])


class NotifyChannelSchemaStillAcceptsMinSeverity(unittest.TestCase):
    def test_legacy_min_severity_key_still_validates(self):
        """기존 notify.local.json 에 min_severity 가 남아 있어도 스키마 검증 실패로 서버가 안 뜨는 일이 없어야 한다."""
        doc = {"channels": [{"name": "legacy", "kind": "webhook", "url": "http://x",
                             "enabled": True, "min_severity": "high"}]}
        self.assertEqual(schemas.validate(doc, schemas.NOTIFY_CHANNELS), [])


class NewFindingsHaveNoSeverityDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg = config.load(None)
            cls.conn = db.connect(cls.cfg.dsn)
            db.require_migrated(cls.conn, cls.cfg.migrations_dir)
        except ConfigError as e:
            raise unittest.SkipTest(f"DB 연결/마이그레이션 불가: {e}") from None
        import json
        if "baseline-demo" not in {t["target_id"] for t in json.loads(
                (cls.cfg.home / "config" / "scope.json").read_text(encoding="utf-8"))["targets"]}:
            raise unittest.SkipTest("scope.json 에 baseline-demo 대상이 없음")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def _run_and_asset(self, operator):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], operator, checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        asset_id = self.conn.execute(
            "SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        return run, asset_id

    def test_llm_confirmed_finding_has_null_severity(self):
        run, asset_id = self._run_and_asset("pytest-severity-llm")
        eid = self.conn.execute(
            "INSERT INTO evidence (run_id, asset_id, requirement_id, tool, args, success, rel_path, sha256, size) "
            "VALUES (%s,%s,'TEST-SEV','test.manual','{}',true,'test/fake.txt',repeat('a',64),0) RETURNING id",
            (run["id"], asset_id)).fetchone()["id"]
        fid = worker.create_llm_finding(self.conn, run["id"], asset_id, title="심각도 폐지 확인",
                                        rationale="근거", remediation="조치", evidence_ids=[eid], model="fake")
        row = self.conn.execute("SELECT severity FROM findings WHERE id=%s", (fid,)).fetchone()
        self.assertIsNone(row["severity"])

    def test_rule_confirmed_finding_has_null_severity(self):
        """worker._create_finding(탐색 기반 승격) 이 실제로 쓰는 INSERT 문을 그대로 재사용해 확인한다."""
        run, asset_id = self._run_and_asset("pytest-severity-rule")
        f = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification) "
            "VALUES (%s,%s,'탐색 기반 발견: SQLI','근거','조치','RULE_CONFIRMED') RETURNING id, severity",
            (run["id"], asset_id)).fetchone()
        self.assertIsNone(f["severity"])

    def test_checklist_fail_finding_has_null_severity(self):
        """worker._run_checklist() 의 FAIL -> finding INSERT 문(severity 없이)을 그대로 재사용해 확인한다."""
        run, asset_id = self._run_and_asset("pytest-severity-checklist")
        f = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification) "
            "VALUES (%s,%s,'체크리스트 FAIL','근거','조치','RULE_CONFIRMED') RETURNING id, severity",
            (run["id"], asset_id)).fetchone()
        self.assertIsNone(f["severity"])

    def test_existing_severity_value_is_preserved(self):
        """기존 데이터 보존 원칙(§11) — 과거에 severity 가 찍힌 행은 이번 변경으로 지워지지 않는다."""
        run, asset_id = self._run_and_asset("pytest-severity-legacy")
        f = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, severity, rationale, remediation, verification) "
            "VALUES (%s,%s,'과거 데이터','high','근거','조치','RULE_CONFIRMED') RETURNING id",
            (run["id"], asset_id)).fetchone()
        row = self.conn.execute("SELECT severity FROM findings WHERE id=%s", (f["id"],)).fetchone()
        self.assertEqual(row["severity"], "high")


if __name__ == "__main__":
    unittest.main()
