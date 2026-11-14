"""G3: 검토자 편집(title/rationale/remediation, UPGRADE_PLAN.md)을 확인한다.

- 바뀐 필드만 finding_revisions 에 한 행씩 남고, 값이 같으면 기록하지 않는다.
- 사유(reason) 없이는 거부된다.
- 허용 밖 필드(예: severity)는 거부된다 — 조용히 무시하지 않는다.
- finding_revisions 는 추가 전용(UPDATE/DELETE 거부, forbid_change 트리거 재사용).
- human_edited=true 가 되면 reporting._findings_for_pentest_report() 가 Reporter 다듬기를 건너뛴다
  (원문을 그대로 쓴다).
"""
import unittest

from audit.models import AuditError, ConfigError
from diagnostic_workbench import config, db, reporting, runs, worker


class FindingEditTest(unittest.TestCase):
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

    def _run_and_asset(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-finding-edit",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        asset_id = self.conn.execute(
            "SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        return run, asset_id

    def _finding(self, run_id, asset_id, **overrides):
        body = {"title": "원래 제목", "rationale": "원래 근거",
               "remediation": "원래 대응방안", "verification": "RULE_CONFIRMED", **overrides}
        return self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (run_id, asset_id, body["title"], body["rationale"], body["remediation"],
             body["verification"])).fetchone()

    def test_edit_records_only_changed_fields(self):
        run, asset_id = self._run_and_asset()
        f = self._finding(run["id"], asset_id)
        changed = worker.edit_finding(self.conn, f["id"], {"title": "새 제목", "rationale": "원래 근거"},
                                      "제목이 부정확함", actor="reviewer:pytest")
        self.assertEqual(changed, ["title"])  # rationale 은 값이 같아 기록 안 됨
        row = self.conn.execute("SELECT * FROM findings WHERE id=%s", (f["id"],)).fetchone()
        self.assertEqual(row["title"], "새 제목")
        self.assertEqual(row["rationale"], "원래 근거")
        self.assertTrue(row["human_edited"])
        rev = self.conn.execute("SELECT * FROM finding_revisions WHERE finding_id=%s", (f["id"],)).fetchall()
        self.assertEqual(len(rev), 1)
        self.assertEqual(rev[0]["field"], "title")
        self.assertEqual(rev[0]["old_value"], "원래 제목")
        self.assertEqual(rev[0]["new_value"], "새 제목")
        self.assertEqual(rev[0]["actor"], "reviewer:pytest")

    def test_no_actual_change_records_nothing(self):
        run, asset_id = self._run_and_asset()
        f = self._finding(run["id"], asset_id)
        changed = worker.edit_finding(self.conn, f["id"], {"title": "원래 제목"}, "사유", actor="reviewer:pytest")
        self.assertEqual(changed, [])
        row = self.conn.execute("SELECT human_edited FROM findings WHERE id=%s", (f["id"],)).fetchone()
        self.assertFalse(row["human_edited"])

    def test_missing_reason_is_rejected(self):
        run, asset_id = self._run_and_asset()
        f = self._finding(run["id"], asset_id)
        with self.assertRaises(AuditError):
            worker.edit_finding(self.conn, f["id"], {"title": "새 제목"}, "   ", actor="reviewer:pytest")

    def test_disallowed_field_is_rejected(self):
        run, asset_id = self._run_and_asset()
        f = self._finding(run["id"], asset_id)
        with self.assertRaises(AuditError):
            worker.edit_finding(self.conn, f["id"], {"severity": "critical"}, "사유", actor="reviewer:pytest")
        row = self.conn.execute("SELECT human_edited FROM findings WHERE id=%s", (f["id"],)).fetchone()
        self.assertFalse(row["human_edited"])  # 거부됐으니 아무것도 안 바뀜

    def test_finding_revisions_table_is_append_only(self):
        run, asset_id = self._run_and_asset()
        f = self._finding(run["id"], asset_id)
        worker.edit_finding(self.conn, f["id"], {"title": "새 제목"}, "사유", actor="reviewer:pytest")
        rev_id = self.conn.execute("SELECT id FROM finding_revisions WHERE finding_id=%s",
                                   (f["id"],)).fetchone()["id"]
        self.conn.commit()  # rollback 뒤에도 이 행이 남아 있어야 DELETE 시도가 실제로 그 행을 건드림
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE finding_revisions SET new_value='조작' WHERE id=%s", (rev_id,))
        self.conn.rollback()
        with self.assertRaises(Exception):
            self.conn.execute("DELETE FROM finding_revisions WHERE id=%s", (rev_id,))
        self.conn.rollback()

    def test_human_edited_finding_skips_reporter_polish(self):
        run, asset_id = self._run_and_asset()
        f = self._finding(run["id"], asset_id, title="편집될 발견")
        worker.edit_finding(self.conn, f["id"], {"rationale": "사람이 다시 쓴 근거"}, "다듬기 확인용",
                            actor="reviewer:pytest")

        class _FakeLLM:
            model = "fake-reporter"

            def structured_chat(self, messages, schema, purpose):
                raise AssertionError("human_edited=true 인 finding 은 Reporter LLM 을 부르면 안 됨")

        meta = {"targets": [{"target_id": "baseline-demo", "name": "baseline-demo", "url": "", "kind": "WEB"}]}
        findings, _ = reporting._findings_for_pentest_report(self.conn, self.cfg.evidence_dir, run, meta,
                                                              llm=_FakeLLM())
        found = next(x for x in findings if x["name"] == "편집될 발견")
        self.assertEqual(found["result"], "사람이 다시 쓴 근거")
        self.assertIn("검토자 수정 반영", found["notes"])


if __name__ == "__main__":
    unittest.main()
