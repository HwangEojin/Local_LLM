"""체크리스트 없이 시작하는 자율 진단(CLAUDE.md §3.2/§5.1/§6)을 확인한다.

- 체크리스트 없이 run create 하려면 --llm(use_llm=True) 이 필요하다(코드/정책 정상 거부).
- 체크리스트 없는 실행은 checklist_version_id 가 NULL 이고, run_checklist_items 없이 goal 노드와
  자산만 등록된다.
- explore.propose 는 체크리스트 증거가 하나도 없어도(known 이 비어 있어도) 체크리스트가 없는 실행이면
  모델을 불러 최초 탐색을 제안하고, 그 제안은 증적 인용 없이도(bootstrap) 받아들여진다.
"""
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db, explore, reporting, runs


class _FakeLLM:
    """explore.propose 가 기대하는 최소 인터페이스. 실제 Ollama 를 부르지 않는다."""
    model = "fake-bootstrap"

    def __init__(self, proposals):
        self._proposals = proposals

    def structured_chat(self, messages, schema, purpose):
        self.last_messages = messages
        return {"proposals": self._proposals}


class CreateRunWithoutChecklist(unittest.TestCase):
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

    def test_requires_llm_without_checklist(self):
        with self.assertRaises(ConfigError):
            runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-autonomous",
                            checklist_id=None, use_llm=False)

    def test_creates_goal_and_assets_without_checklist_items(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-autonomous",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)  # create_run 이 이미 commit 했으므로 이후 조회만 함
        self.assertIsNone(run["checklist_version_id"])
        rci_count = self.conn.execute(
            "SELECT count(*) AS n FROM run_checklist_items WHERE run_id=%s", (run["id"],)).fetchone()["n"]
        self.assertEqual(rci_count, 0)
        goal = self.conn.execute(
            "SELECT payload FROM exploration_nodes WHERE run_id=%s AND kind='goal'", (run["id"],)).fetchone()
        self.assertIn("자율 진단", goal["payload"]["text"])

    def test_explore_propose_bootstraps_without_prior_evidence(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-autonomous",
                              checklist_id=None, use_llm=True)
        ctx = runs.build_context(self.cfg, self.conn, run)
        ctx.llm = _FakeLLM([{
            "rationale": "최초 정찰", "target_id": "baseline-demo", "tool": "mcp:frida/list_processes",
            "args": {}, "risk": "read", "expected_evidence": "프로세스 목록",
            "stop_condition": "목록 확인", "based_on_evidence_ids": [],
        }])
        accepted = explore.propose(ctx)
        self.assertEqual(accepted, 1)
        n = self.conn.execute(
            "SELECT count(*) AS n FROM backlog_items WHERE run_id=%s AND kind='exploration'",
            (run["id"],)).fetchone()["n"]
        self.assertEqual(n, 1)

    def test_report_generate_without_checklist_produces_exploration_report(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-autonomous",
                              checklist_id=None, use_llm=True)
        self.conn.execute("UPDATE runs SET status='COMPLETED', ended_at=now() WHERE id=%s", (run["id"],))
        self.conn.commit()
        run = db.resolve_run(self.conn, str(run["id"]))
        res = reporting.generate(self.cfg, self.conn, run)
        self.assertTrue(res["warnings"])  # PASS/FAIL 없음 경고가 있어야 함
        from docx import Document
        from openpyxl import load_workbook
        doc = Document(res["docx"])
        self.assertTrue(any("초안" in p.text for p in doc.paragraphs))
        wb = load_workbook(res["xlsx"], read_only=True)
        self.assertEqual(set(wb.sheetnames), {"개요", "탐색", "발견사항", "증적"})


if __name__ == "__main__":
    unittest.main()
