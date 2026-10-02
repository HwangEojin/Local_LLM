import json
import unittest

from audit.checklist_engine import Checklists, validate_content
from audit.models import ConfigError, PolicyDenied
from tests.helpers import PLAN, Home

EXPECTED = {"SAMPLE-001": "FAIL", "SAMPLE-002": "FAIL", "SAMPLE-003": "PASS",
            "SAMPLE-004": "MANUAL_REVIEW", "SAMPLE-005": "MANUAL_REVIEW",
            "SAMPLE-006": "NOT_APPLICABLE"}


class FakeLLM:
    """OllamaClient.structured_chat 대역: 주어진 증거를 인용해 고정된 판정을 반환한다."""
    def __init__(self, verdict):
        self.verdict = verdict

    def structured_chat(self, messages, schema, purpose):
        import re
        ids = re.findall(r'<evidence id="([^"]+)"', messages[1]["content"])
        return {"verdict": self.verdict, "rationale": "fake", "evidence_ids": ids}


class ChecklistTest(unittest.TestCase):
    def setUp(self):
        self.h = Home()

    def tearDown(self):
        self.h.close()

    def test_shipped_checklist_valid_and_marked_non_official(self):
        data, sha = self.h.checklists.load("sample-local-config", "0.1.0")
        self.assertFalse(data["official"])
        self.assertIn("공식 가이드 아님", data["guide_name"])

    def test_invalid_schema_rejected(self):
        data, _ = self.h.checklists.load("sample-local-config", "0.1.0")
        bad = json.loads(json.dumps(data))
        del bad["items"][0]["pass_criteria"]
        self.assertTrue(validate_content(bad))
        bad = json.loads(json.dumps(data))
        bad["items"][0]["verification_method"]["rule"]["evidence"] = "nope"
        self.assertTrue(validate_content(bad))
        bad = json.loads(json.dumps(data))
        bad["items"][1]["check_id"] = bad["items"][0]["check_id"]
        self.assertTrue(validate_content(bad))
        bad = json.loads(json.dumps(data))
        bad["items"][0]["pass_criteria"] = ""  # 기준이 없는 완성 항목
        self.assertTrue(validate_content(bad))

    def test_modified_after_activation_is_refused(self):
        p = self.h.path / "checklist/versions/sample-local-config/0.1.0.json"
        p.write_text(p.read_text(encoding="utf-8").replace("(예시)", "(changed)", 1), encoding="utf-8")
        with self.assertRaises(ConfigError):
            Checklists(self.h.path / "checklist").load("sample-local-config", "0.1.0")

    def test_new_version_activation_preserves_old_results(self):
        orch = self.h.orchestrator()
        run1 = orch.start(self.h.resolve())
        src = self.h.path / "checklist/versions/sample-local-config"
        data = json.loads((src / "0.1.0.json").read_text(encoding="utf-8"))
        data["guide_version"] = "0.2.0-sample"
        (src / "0.2.0.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        man = json.loads((self.h.path / "checklist/manifest.json").read_text(encoding="utf-8"))
        man["checklists"][0]["versions"].append(
            {"version": "0.2.0", "path": "versions/sample-local-config/0.2.0.json", "status": "draft"})
        (self.h.path / "checklist/manifest.json").write_text(json.dumps(man), encoding="utf-8")
        cl = Checklists(self.h.path / "checklist")
        with self.assertRaises(ConfigError):  # draft 는 실행할 수 없다
            cl.load("sample-local-config", "0.2.0")
        cl.activate("sample-local-config", "0.2.0")
        cl = Checklists(self.h.path / "checklist")
        with self.assertRaises(ConfigError):  # 이전 버전은 superseded 이므로 실행 불가
            cl.load("sample-local-config", "0.1.0")
        cl.load("sample-local-config", "0.1.0", for_run=False)  # 하지만 읽기/검증은 가능
        self.assertEqual({r["checklist_version"] for r in self.h.storage.results(run1)}, {"0.1.0"})


class RunTest(unittest.TestCase):
    def setUp(self):
        self.h = Home()

    def tearDown(self):
        self.h.close()

    def verdicts(self, run_id):
        return {r["check_id"]: r["verdict"] for r in self.h.storage.results(run_id)}

    def test_full_run_deterministic(self):
        run_id = self.h.orchestrator().start(self.h.resolve())
        self.assertEqual(self.verdicts(run_id), EXPECTED)
        self.assertEqual(self.h.storage.get_run(run_id)["status"], "COMPLETED")
        r = self.h.storage.get_result(run_id, "SAMPLE-001", "sample-app")
        ev_ids = json.loads(r["evidence_ids"])
        self.assertEqual(len(ev_ids), 1)
        ev = self.h.storage.evidence(evidence_id=ev_ids[0])
        self.assertEqual(len(ev["sha256"]), 64)
        self.assertNotIn("changeme123", r["rule_outcome"])  # 마스킹된 발췌

    def test_missing_evidence_file_is_inconclusive_not_pass(self):
        (self.h.path / "examples/target_app/app.conf").unlink()
        run_id = self.h.orchestrator().start(self.h.resolve())
        v = self.verdicts(run_id)
        for cid in ("SAMPLE-001", "SAMPLE-002", "SAMPLE-003"):
            self.assertEqual(v[cid], "INCONCLUSIVE")

    def test_llm_disagreement_goes_manual(self):
        run_id = self.h.orchestrator(llm=FakeLLM("PASS")).start(self.h.resolve())
        v = self.verdicts(run_id)
        self.assertEqual(v["SAMPLE-001"], "MANUAL_REVIEW")  # 규칙 FAIL vs LLM PASS
        self.assertEqual(v["SAMPLE-003"], "PASS")           # 일치
        self.assertEqual(v["SAMPLE-004"], "MANUAL_REVIEW")  # 수동 항목은 LLM이 확정하지 않음

    def test_out_of_scope_plan_rejected(self):
        with self.assertRaises(PolicyDenied):
            self.h.resolve({**PLAN, "targets": ["not-in-scope"]})
        with self.assertRaises(ConfigError):
            self.h.resolve({**PLAN, "check_ids": ["NOPE"]})

    def test_requires_approval_field_grants_nothing(self):
        # 계획의 requires_approval=false 는 아무것도 우회할 수 없다: 미등록 도구는 계속 거부된다.
        data, _ = self.h.checklists.load("sample-local-config", "0.1.0")
        self.h.registry.pop("fs.read_file")
        with self.assertRaises(PolicyDenied):
            self.h.resolve({**PLAN, "requires_approval": False})

    def test_resume_skips_done_items(self):
        orch = self.h.orchestrator()
        resolved = self.h.resolve()
        calls = {"n": 0}
        real = orch._process

        def flaky(item, target):
            calls["n"] += 1
            if calls["n"] == 3:
                raise KeyboardInterrupt
            real(item, target)
        orch._process = flaky
        with self.assertRaises(KeyboardInterrupt):
            orch.start(resolved)
        run_id = orch.run_id
        self.assertEqual(self.h.storage.get_run(run_id)["status"], "ABORTED")
        self.assertEqual(len(self.h.storage.results(run_id)), 2)
        self.h.orchestrator().resume(run_id, resolved)
        self.assertEqual(self.verdicts(run_id), EXPECTED)
        # 이미 끝난 항목은 중복 판정하지 않는다
        hist = [h["check_id"] for h in self.h.storage.history(run_id)]
        self.assertEqual(len(hist), len(set(hist)))

    def test_llm_rationale_secrets_are_redacted(self):
        """LLM 이 근거에 비밀 값을 그대로 인용해도 DB 와 보고서에는 남지 않는다."""
        class Leaky(FakeLLM):
            def structured_chat(self, messages, schema, purpose):
                out = super().structured_chat(messages, schema, purpose)
                out["rationale"] = "db_password 키에 평문 값 'changeme123' 이 있다."
                return out
        run_id = self.h.orchestrator(llm=Leaky("FAIL")).start(self.h.resolve())
        rows = [r for r in self.h.storage.results(run_id) if r["llm_outcome"]]
        self.assertTrue(rows)
        for r in rows:
            self.assertNotIn("changeme123", r["llm_outcome"])
        self.assertIn("****", rows[0]["llm_outcome"])
        from audit import report
        res = report.generate(self.h.storage, run_id, self.h.checklists, self.h.settings, self.h.evidence)
        from docx import Document
        text = "\n".join(p.text for p in Document(res["docx"]).paragraphs)
        self.assertNotIn("changeme123", text)


if __name__ == "__main__":
    unittest.main()
