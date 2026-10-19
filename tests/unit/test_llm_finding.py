"""LLM의 직접 finding 확정(CLAUDE.md §1.1 예외 3번, 2026-10-19)을 확인한다.

- LLM이 실제로 존재하는 증거 id를 인용하면 verification='LLM_CONFIRMED', status='CONFIRMED'로 즉시 기록된다.
- 실존하지 않는(인용한) 증거 id로는 기록되지 않는다(최소한의 데이터 무결성).
- 기록된 finding은 finding_review_history에 actor='llm:<model>'로 추적 가능하게 남는다.
- 사람은 이후에도 finding review로 재검토할 수 있다(worker.set_finding_status 재사용, 지난 세션에서 확인됨).
"""
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db, explore, runs


class _FakeLLM:
    model = "fake-llm-finding"

    def __init__(self, response):
        self._response = response

    def structured_chat(self, messages, schema, purpose):
        return self._response


class LlmFindingTest(unittest.TestCase):
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

    def _run_with_known_evidence(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-llm-finding",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        asset_id = self.conn.execute(
            "SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        eid = self.conn.execute(
            "INSERT INTO evidence (run_id, asset_id, requirement_id, tool, args, success, rel_path, sha256, size) "
            "VALUES (%s,%s,'TEST-LLM-FINDING','test.manual','{}',true,'test/fake.txt',repeat('a',64),0) "
            "RETURNING id", (run["id"], asset_id)).fetchone()["id"]
        # worker._tool_calls_used/_context 는 FACTS/RESULTS 에서 known 을 뽑으므로, 테스트는 explore._context
        # 를 거치지 않고 직접 known/assets 을 구성해 _report_llm_finding 을 부른다(단위 테스트 범위를 좁힘).
        ctx = runs.build_context(self.cfg, self.conn, run)
        return run, ctx, asset_id, str(eid)

    def test_cited_real_evidence_is_confirmed_immediately(self):
        run, ctx, asset_id, eid = self._run_with_known_evidence()
        ctx.llm = _FakeLLM({})
        assets = {"baseline-demo": asset_id}
        explore._report_llm_finding(ctx, {
            "target_id": "baseline-demo", "title": "LLM 직접 확정 테스트", "severity": "high",
            "rationale": "증거에서 직접 확인됨", "remediation": "패치", "evidence_ids": [eid],
        }, assets, {eid})
        row = self.conn.execute(
            "SELECT * FROM findings WHERE run_id=%s AND title='LLM 직접 확정 테스트'", (run["id"],)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "CONFIRMED")
        self.assertEqual(row["verification"], "LLM_CONFIRMED")
        self.assertEqual(row["reviewed_by"], f"llm:{ctx.llm.model}")
        linked = self.conn.execute(
            "SELECT count(*) AS n FROM finding_evidence WHERE finding_id=%s", (row["id"],)).fetchone()["n"]
        self.assertEqual(linked, 1)
        hist = self.conn.execute(
            "SELECT old_status, new_status, actor FROM finding_review_history WHERE finding_id=%s",
            (row["id"],)).fetchone()
        self.assertEqual(hist["new_status"], "CONFIRMED")
        self.assertEqual(hist["actor"], f"llm:{ctx.llm.model}")

    def test_citing_nonexistent_evidence_is_not_recorded(self):
        run, ctx, asset_id, eid = self._run_with_known_evidence()
        ctx.llm = _FakeLLM({})
        assets = {"baseline-demo": asset_id}
        fake_id = "00000000-0000-0000-0000-000000000000"
        explore._report_llm_finding(ctx, {
            "target_id": "baseline-demo", "title": "존재하지 않는 증거 테스트", "severity": "high",
            "rationale": "실존하지 않는 증거를 인용", "remediation": "-", "evidence_ids": [fake_id],
        }, assets, {fake_id})  # known 에도 넣어 인용 검증은 통과시키되, 실제 evidence 행이 없음을 확인
        row = self.conn.execute(
            "SELECT * FROM findings WHERE run_id=%s AND title='존재하지 않는 증거 테스트'", (run["id"],)).fetchone()
        self.assertIsNone(row)

    def test_target_outside_known_is_rejected(self):
        run, ctx, asset_id, eid = self._run_with_known_evidence()
        ctx.llm = _FakeLLM({})
        explore._report_llm_finding(ctx, {
            "target_id": "no-such-target", "title": "범위 밖 테스트", "severity": "low",
            "rationale": "x", "remediation": "-", "evidence_ids": [eid],
        }, {}, {eid})
        row = self.conn.execute(
            "SELECT * FROM findings WHERE run_id=%s AND title='범위 밖 테스트'", (run["id"],)).fetchone()
        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
