"""G2: LLM 판정 교차 검증 게이트(UPGRADE_PLAN.md)를 확인한다.

- run.verifier_model 이 있으면 LLM 제안 finding 은 바로 확정되지 않고 LLM_PROPOSED/NEEDS_REVIEW 로
  남는다(proposer 표 기록) → 검증 모델이 같은 증거만 보고 독립 판정.
- CONFIRMS: LLM_CONFIRMED/CONFIRMED 로 승격, actor에 양쪽 모델 모두 기록, 알림 전송.
- REFUTES/INSUFFICIENT/검증 모델 오류: NEEDS_REVIEW 유지, 알림 없음, 이력에 사유 기록.
- 검증 모델이 제공받지 않은 증거 id를 인용하면 Python 이 걸러낸다(지어낸 인용 무시).
- verifier_model 이 None 이면 기존 동작(즉시 확정)과 완전히 같다(test_llm_finding.py 가 이미 확인).
- 실행 생성 시 교차 검증 모델이 제안 모델(planner)과 같으면 거부된다.
"""
import json
import unittest
from unittest import mock

from audit.local_model import LLMError
from audit.models import ConfigError
from diagnostic_workbench import config, db, explore, runs


class _FakeLLM:
    def __init__(self, model, response=None, error: str | None = None):
        self.model = model
        self._response, self._error = response, error

    def structured_chat(self, messages, schema, purpose):
        if self._error:
            raise LLMError(self._error)
        return self._response


class LlmCrossCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg = config.load(None)
            cls.conn = db.connect(cls.cfg.dsn)
            db.require_migrated(cls.conn, cls.cfg.migrations_dir)
        except ConfigError as e:
            raise unittest.SkipTest(f"DB 연결/마이그레이션 불가: {e}") from None
        if "baseline-demo" not in {t["target_id"] for t in json.loads(
                (cls.cfg.home / "config" / "scope.json").read_text(encoding="utf-8"))["targets"]}:
            raise unittest.SkipTest("scope.json 에 baseline-demo 대상이 없음")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def _run_with_known_evidence(self, verifier_model="fake:verifier"):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-llm-cross-check",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        asset_id = self.conn.execute(
            "SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        eid = self.conn.execute(
            "INSERT INTO evidence (run_id, asset_id, requirement_id, tool, args, success, rel_path, sha256, size) "
            "VALUES (%s,%s,'TEST-CROSS-CHECK','test.manual','{}',true,'test/fake.txt',repeat('b',64),0) "
            "RETURNING id", (run["id"], asset_id)).fetchone()["id"]
        ctx = runs.build_context(self.cfg, self.conn, run)
        ctx.run["verifier_model"] = verifier_model  # DB 에 쓰지 않고 ctx 에서만 바꿈(단위 테스트 범위를 좁힘)
        return run, ctx, asset_id, str(eid)

    def _report(self, ctx, assets, eid, title):
        explore._report_llm_finding(ctx, {
            "target_id": "baseline-demo", "title": title, "vuln_class": "OTHER",
            "rationale": "교차 검증 테스트", "remediation": "패치", "evidence_ids": [eid],
        }, assets, {eid})
        return self.conn.execute("SELECT * FROM findings WHERE run_id=%s AND title=%s",
                                 (ctx.run["id"], title)).fetchone()

    def _votes(self, finding_id):
        return self.conn.execute(
            "SELECT stage, verdict, model FROM finding_llm_votes WHERE finding_id=%s ORDER BY stage",
            (finding_id,)).fetchall()

    def test_verifier_confirms_promotes_with_both_actors_and_notifies(self):
        run, ctx, asset_id, eid = self._run_with_known_evidence()
        ctx.llm = _FakeLLM("proposer-model")
        verifier = _FakeLLM("verifier-model", {"verdict": "CONFIRMS", "rationale": "동의함", "evidence_ids": [eid]})
        with mock.patch.object(explore, "make_llm_for_spec", return_value=verifier):
            row = self._report(ctx, {"baseline-demo": asset_id}, eid, "교차검증-동의")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "CONFIRMED")
        self.assertEqual(row["verification"], "LLM_CONFIRMED")
        votes = self._votes(row["id"])
        self.assertEqual([(v["stage"], v["verdict"]) for v in votes],
                         [("proposer", "CONFIRMS"), ("verifier", "CONFIRMS")])
        rh = self.conn.execute(
            "SELECT actor FROM finding_review_history WHERE finding_id=%s ORDER BY id DESC LIMIT 1",
            (row["id"],)).fetchone()
        self.assertEqual(rh["actor"], "llm:proposer-model+llm:verifier-model")
        ev = self.conn.execute(
            "SELECT details FROM audit_events WHERE run_id=%s AND event='llm_finding_cross_checked'",
            (run["id"],)).fetchone()
        self.assertEqual(ev["details"]["verdict"], "CONFIRMS")

    def test_verifier_refutes_keeps_needs_review_without_promoting(self):
        run, ctx, asset_id, eid = self._run_with_known_evidence()
        ctx.llm = _FakeLLM("proposer-model")
        verifier = _FakeLLM("verifier-model", {"verdict": "REFUTES", "rationale": "증거가 반박함", "evidence_ids": []})
        with mock.patch.object(explore, "make_llm_for_spec", return_value=verifier):
            row = self._report(ctx, {"baseline-demo": asset_id}, eid, "교차검증-반박")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "NEEDS_REVIEW")
        self.assertEqual(row["verification"], "LLM_PROPOSED")
        rh = self.conn.execute(
            "SELECT new_status FROM finding_review_history WHERE finding_id=%s ORDER BY id DESC LIMIT 1",
            (row["id"],)).fetchone()
        self.assertEqual(rh["new_status"], "NEEDS_REVIEW")

    def test_verifier_insufficient_keeps_needs_review(self):
        run, ctx, asset_id, eid = self._run_with_known_evidence()
        ctx.llm = _FakeLLM("proposer-model")
        verifier = _FakeLLM("verifier-model", {"verdict": "INSUFFICIENT", "rationale": "불충분", "evidence_ids": []})
        with mock.patch.object(explore, "make_llm_for_spec", return_value=verifier):
            row = self._report(ctx, {"baseline-demo": asset_id}, eid, "교차검증-불충분")
        self.assertEqual(row["status"], "NEEDS_REVIEW")
        votes = self._votes(row["id"])
        self.assertIn(("verifier", "INSUFFICIENT"), [(v["stage"], v["verdict"]) for v in votes])

    def test_verifier_llm_error_is_treated_as_insufficient_and_does_not_raise(self):
        run, ctx, asset_id, eid = self._run_with_known_evidence()
        ctx.llm = _FakeLLM("proposer-model")
        verifier = _FakeLLM("verifier-model", error="연결 실패")
        with mock.patch.object(explore, "make_llm_for_spec", return_value=verifier):
            row = self._report(ctx, {"baseline-demo": asset_id}, eid, "교차검증-오류")
        self.assertEqual(row["status"], "NEEDS_REVIEW")
        votes = self._votes(row["id"])
        self.assertIn("INSUFFICIENT", [v["verdict"] for v in votes])

    def test_forged_citation_outside_provided_evidence_is_dropped(self):
        run, ctx, asset_id, eid = self._run_with_known_evidence()
        fake_id = "00000000-0000-0000-0000-000000000000"
        verifier = _FakeLLM("verifier-model", {"verdict": "CONFIRMS", "rationale": "지어낸 인용", "evidence_ids": [fake_id]})
        with mock.patch.object(explore, "make_llm_for_spec", return_value=verifier):
            result = explore.verify_llm_finding(ctx, vuln_class="OTHER", title="t", target_id="baseline-demo",
                                                locator={}, evidence_rows=[{"id": eid, "tool": "test.manual", "data": b""}])
        self.assertEqual(result["evidence_ids"], [])  # 제공하지 않은 id 는 걸러짐

    def test_create_run_rejects_verifier_model_same_as_proposer(self):
        installed = explore.installed_ollama_models(self.cfg)
        if not installed:
            raise unittest.SkipTest("Ollama 에 연결할 수 없거나 설치된 모델이 없음")
        spec = f"ollama:{sorted(installed)[0]}"
        with self.assertRaises(ConfigError):
            runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-cross-check-reject",
                            checklist_id=None, use_llm=True, agent_models={"planner": spec}, verifier_model=spec)


if __name__ == "__main__":
    unittest.main()
