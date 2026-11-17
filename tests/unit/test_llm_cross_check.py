"""G2: LLM 판정 교차 검증 게이트(UPGRADE_PLAN.md)를 확인한다.

- run.verifier_model 이 있으면 LLM 제안 finding 은 바로 확정되지 않고 LLM_PROPOSED/NEEDS_REVIEW 로
  남는다(proposer 표 기록) → 검증 모델이 같은 증거만 보고 독립 판정.
- CONFIRMS: LLM_CONFIRMED/CONFIRMED 로 승격, actor에 양쪽 모델 모두 기록, 알림 전송.
- REFUTES/INSUFFICIENT/ERROR(검증 모델 오류): NEEDS_REVIEW 유지, 알림 없음, 이력에 사유 기록.
- H3: 유효한 인용 없는 CONFIRMS 는 INSUFFICIENT, 사람이 먼저 바꾼 상태는 덮어쓰지 않음, 같은 모델 검사는
  문자열이 아니라 실제 모델로.
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

    def test_verifier_llm_error_is_recorded_as_error_and_does_not_raise(self):
        """H3: 호출 실패는 INSUFFICIENT 가 아니라 ERROR 로 남는다(판단 불가와 구분) — 상태는 NEEDS_REVIEW 유지."""
        run, ctx, asset_id, eid = self._run_with_known_evidence()
        ctx.llm = _FakeLLM("proposer-model")
        verifier = _FakeLLM("verifier-model", error="연결 실패")
        with mock.patch.object(explore, "make_llm_for_spec", return_value=verifier):
            row = self._report(ctx, {"baseline-demo": asset_id}, eid, "교차검증-오류")
        self.assertEqual(row["status"], "NEEDS_REVIEW")
        votes = self._votes(row["id"])
        self.assertIn("ERROR", [v["verdict"] for v in votes])
        self.assertNotIn("INSUFFICIENT", [v["verdict"] for v in votes])

    def test_forged_citation_outside_provided_evidence_is_dropped(self):
        run, ctx, asset_id, eid = self._run_with_known_evidence()
        fake_id = "00000000-0000-0000-0000-000000000000"
        verifier = _FakeLLM("verifier-model", {"verdict": "CONFIRMS", "rationale": "지어낸 인용", "evidence_ids": [fake_id]})
        with mock.patch.object(explore, "make_llm_for_spec", return_value=verifier):
            result = explore.verify_llm_finding(ctx, vuln_class="OTHER", title="t", target_id="baseline-demo",
                                                locator={}, evidence_rows=[{"id": eid, "tool": "test.manual", "data": b""}])
        self.assertEqual(result["evidence_ids"], [])  # 제공하지 않은 id 는 걸러짐
        self.assertEqual(result["verdict"], "INSUFFICIENT")  # H3: 유효한 인용 없는 동의는 동의로 치지 않음

    def test_forged_citation_confirms_does_not_promote(self):
        """H3 재현: 고치기 전에는 지어낸 인용으로 CONFIRMS 하면 그대로 확정됐다."""
        run, ctx, asset_id, eid = self._run_with_known_evidence()
        ctx.llm = _FakeLLM("proposer-model")
        verifier = _FakeLLM("verifier-model", {"verdict": "CONFIRMS", "rationale": "근거 없이 동의",
                                               "evidence_ids": ["00000000-0000-0000-0000-000000000000"]})
        with mock.patch.object(explore, "make_llm_for_spec", return_value=verifier), \
                mock.patch.object(explore.notify, "notify_finding_confirmed") as notified:
            row = self._report(ctx, {"baseline-demo": asset_id}, eid, "교차검증-위조인용")
        self.assertEqual((row["verification"], row["status"]), ("LLM_PROPOSED", "NEEDS_REVIEW"))
        self.assertIn("INSUFFICIENT", [v["verdict"] for v in self._votes(row["id"])])
        notified.assert_not_called()

    def test_promote_does_not_override_human_decision(self):
        """H3: 사람이 먼저 기각했으면 교차 검증 동의가 와도 확정하지 않는다(이력만)."""
        from diagnostic_workbench import worker
        run, ctx, asset_id, eid = self._run_with_known_evidence()
        fid = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification, status) "
            "VALUES (%s,%s,'사람이 먼저 기각','r','m','LLM_PROPOSED','NEEDS_REVIEW') RETURNING id",
            (run["id"], asset_id)).fetchone()["id"]
        self.conn.execute("INSERT INTO finding_evidence VALUES (%s,%s)", (fid, eid))
        worker.set_finding_status(self.conn, fid, "REJECTED", "오탐", actor="reviewer:pytest")
        promoted = worker.promote_llm_proposed(self.conn, fid, actor="llm:a+llm:b", reason="동의")
        self.assertFalse(promoted)
        row = self.conn.execute("SELECT status, verification FROM findings WHERE id=%s", (fid,)).fetchone()
        self.assertEqual((row["status"], row["verification"]), ("REJECTED", "LLM_PROPOSED"))

    def test_same_model_check_resolves_specs_not_strings(self):
        """H3: 표기만 다른 같은 모델(실행 기본 모델 vs 'ollama:<같은 모델>', ':latest' 유무, 프로파일 이름)을
        같은 모델로 본다. Ollama 연결 없이 해석 규칙만 확인한다."""
        default_model = self.cfg.settings.ollama["model"]
        a = runs._resolved_model(self.cfg, None, "ollama", None)
        b = runs._resolved_model(self.cfg, f"ollama:{default_model}", "ollama", None)
        c = runs._resolved_model(self.cfg, f"ollama:{default_model}:latest", "ollama", None) \
            if not default_model.endswith(":latest") else b
        self.assertTrue(runs._same_model_or_unclear(a, b))
        self.assertTrue(runs._same_model_or_unclear(b, c))
        profiles_path = self.cfg.home / "config" / "llm_profiles.json"
        backup = profiles_path.read_text(encoding="utf-8") if profiles_path.exists() else None
        profiles_path.write_text(json.dumps({"profiles": [
            {"name": "same-as-default", "kind": "ollama", "model": default_model},
            {"name": "cli-default", "kind": "claude_cli"}]}), encoding="utf-8")
        self.addCleanup(lambda: profiles_path.write_text(backup, encoding="utf-8") if backup is not None
                        else profiles_path.unlink(missing_ok=True))
        p = runs._resolved_model(self.cfg, "same-as-default", "ollama", None)
        self.assertTrue(runs._same_model_or_unclear(a, p))
        other = runs._resolved_model(self.cfg, "ollama:some-other-model", "ollama", None)
        self.assertFalse(runs._same_model_or_unclear(a, other))
        cli = runs._resolved_model(self.cfg, "cli-default", "ollama", None)
        self.assertFalse(runs._same_model_or_unclear(a, cli))  # 종류가 다르면 확실히 다른 모델
        self.assertTrue(runs._same_model_or_unclear(cli, ("claude_cli", "claude-x")))  # 같은 종류·이름 모름 → 거부

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
