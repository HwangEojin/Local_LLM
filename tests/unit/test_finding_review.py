"""사람의 Finding 검토 워크플로(CLAUDE.md §9/§10)를 확인한다.

- findings.status 는 CANDIDATE 로 시작한다(새 상태 체계).
- CONFIRMED 는 연결된 증거가 있어야만 허용된다 — 규칙/LLM 의 주장만으로는 확정할 수 없다.
- REJECTED/NEEDS_REVIEW/INCONCLUSIVE 는 증거 없이도 가능하다(사람이 오탐이라 판단하거나 보류하는 경우).
- 모든 상태 변경은 finding_review_history 에 추가 전용으로 남고, 기존 findings 행과 이력은 지워지지 않는다.
- 보고서용 변환(_findings_for_pentest_report)은 REJECTED 를 본문에서 뺀다(CLAUDE.md §3.3).
"""
import unittest

from audit.models import AuditError, ConfigError
from diagnostic_workbench import config, db, reporting, runs, worker


class FindingReviewTest(unittest.TestCase):
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
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-finding-review",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        asset_id = self.conn.execute(
            "SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        return run, asset_id

    def _finding(self, run_id, asset_id, **overrides):
        body = {"title": "테스트 발견", "severity": "high", "rationale": "신호 일치",
                "remediation": "패치", "verification": "RULE_CONFIRMED", **overrides}
        return self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, severity, rationale, remediation, verification) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (run_id, asset_id, body["title"], body["severity"], body["rationale"], body["remediation"],
             body["verification"])).fetchone()

    def _evidence(self, run_id, asset_id):
        return self.conn.execute(
            "INSERT INTO evidence (run_id, asset_id, requirement_id, tool, args, success, rel_path, sha256, size) "
            "VALUES (%s,%s,'TEST-REVIEW','test.manual','{}',true,'test/fake.txt',repeat('a',64),0) RETURNING id",
            (run_id, asset_id)).fetchone()["id"]

    def test_new_finding_starts_as_candidate(self):
        run, asset_id = self._run_and_asset()
        f = self._finding(run["id"], asset_id)
        self.assertEqual(f["status"], "CANDIDATE")

    def test_confirmed_requires_evidence(self):
        run, asset_id = self._run_and_asset()
        f = self._finding(run["id"], asset_id)
        with self.assertRaises(AuditError):
            worker.set_finding_status(self.conn, f["id"], "CONFIRMED", "증거 없이 확정 시도",
                                      actor="reviewer:pytest")

    def test_confirmed_with_linked_evidence_succeeds_and_keeps_history(self):
        run, asset_id = self._run_and_asset()
        f = self._finding(run["id"], asset_id)
        eid = self._evidence(run["id"], asset_id)
        worker.set_finding_status(self.conn, f["id"], "CONFIRMED", "증거 확인됨",
                                  actor="reviewer:pytest", evidence_ids=[eid])
        row = self.conn.execute("SELECT * FROM findings WHERE id=%s", (f["id"],)).fetchone()
        self.assertEqual(row["status"], "CONFIRMED")
        self.assertEqual(row["reviewed_by"], "reviewer:pytest")
        linked = self.conn.execute(
            "SELECT count(*) AS n FROM finding_evidence WHERE finding_id=%s", (f["id"],)).fetchone()["n"]
        self.assertEqual(linked, 1)
        hist = self.conn.execute(
            "SELECT old_status, new_status FROM finding_review_history WHERE finding_id=%s", (f["id"],)).fetchall()
        self.assertEqual([(h["old_status"], h["new_status"]) for h in hist], [("CANDIDATE", "CONFIRMED")])

    def test_rejected_does_not_require_evidence(self):
        run, asset_id = self._run_and_asset()
        f = self._finding(run["id"], asset_id)
        worker.set_finding_status(self.conn, f["id"], "REJECTED", "오탐으로 확인됨", actor="reviewer:pytest")
        row = self.conn.execute("SELECT status FROM findings WHERE id=%s", (f["id"],)).fetchone()
        self.assertEqual(row["status"], "REJECTED")

    def test_unknown_finding_id_raises(self):
        with self.assertRaises(AuditError):
            worker.set_finding_status(self.conn, "00000000-0000-0000-0000-000000000000", "REJECTED", "x",
                                      actor="reviewer:pytest")

    def test_report_excludes_rejected_and_counts_by_status(self):
        run, asset_id = self._run_and_asset()
        confirmed = self._finding(run["id"], asset_id, title="확정될 발견")
        eid = self._evidence(run["id"], asset_id)
        worker.set_finding_status(self.conn, confirmed["id"], "CONFIRMED", "증거 확인됨",
                                  actor="reviewer:pytest", evidence_ids=[eid])
        rejected = self._finding(run["id"], asset_id, title="오탐")
        worker.set_finding_status(self.conn, rejected["id"], "REJECTED", "오탐 확인", actor="reviewer:pytest")
        self._finding(run["id"], asset_id, title="아직 미검토")  # CANDIDATE 로 남음
        meta = {"targets": [{"target_id": "baseline-demo", "name": "baseline-demo", "url": "", "kind": "WEB"}]}
        findings, status_counts = reporting._findings_for_pentest_report(self.conn, run, meta)
        titles = {f["name"] for f in findings}
        self.assertIn("확정될 발견", titles)
        self.assertIn("아직 미검토", titles)
        self.assertNotIn("오탐", titles)
        self.assertEqual(status_counts, {"CONFIRMED": 1, "CANDIDATE": 1})

    def test_retest_requeues_original_tool_call_and_logs_request(self):
        run, asset_id = self._run_and_asset()
        f = self._finding(run["id"], asset_id)
        eid = self.conn.execute(
            "INSERT INTO evidence (run_id, asset_id, requirement_id, tool, args, success, rel_path, sha256, size) "
            "VALUES (%s,%s,'TEST-RETEST','test.retest-tool','{\"x\":1}',true,'test/fake.txt',repeat('a',64),0) "
            "RETURNING id", (run["id"], asset_id)).fetchone()["id"]
        self.conn.execute("INSERT INTO finding_evidence VALUES (%s,%s)", (f["id"], eid))
        item_ids = worker.create_retest(self.conn, run["id"], f["id"], operator="pytest", reason="재현 재확인 필요")
        self.assertEqual(len(item_ids), 1)
        item = self.conn.execute("SELECT * FROM backlog_items WHERE id=%s", (item_ids[0],)).fetchone()
        self.assertEqual(item["kind"], "exploration")
        self.assertEqual(item["payload"]["tool"], "test.retest-tool")
        self.assertEqual(item["payload"]["args"], {"x": 1})
        hist = self.conn.execute(
            "SELECT old_status, new_status, actor, reason FROM finding_review_history WHERE finding_id=%s "
            "ORDER BY changed_at DESC LIMIT 1", (f["id"],)).fetchone()
        self.assertEqual(hist["old_status"], hist["new_status"])  # 상태는 바뀌지 않음, 요청만 기록됨
        self.assertEqual(hist["actor"], "reviewer:pytest")
        self.assertIn("재검증 요청됨", hist["reason"])

    def test_retest_without_evidence_raises(self):
        run, asset_id = self._run_and_asset()
        f = self._finding(run["id"], asset_id)
        with self.assertRaises(AuditError):
            worker.create_retest(self.conn, run["id"], f["id"], operator="pytest", reason="x")


if __name__ == "__main__":
    unittest.main()
