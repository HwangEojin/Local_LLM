"""거부된 탐색 제안도 기억하는지 확인한다(CLAUDE.md §6 "동일한 실패를 무한 반복하지 않는다").

이전에는 거부된 제안(policy DENY, 등록되지 않은
도구 등)이 audit_events 에만 남고 다음 라운드 프롬프트(ALREADY_TRIED)에는 전혀 보이지 않아, LLM 이
똑같이 거부될 제안을 라운드마다 다시 낼 수 있었다(실행되진 않으니 안전하지만 라운드 낭비). 이제는
exploration_nodes(kind='intent', state='REJECTED')로 가볍게 남기고 _context() 가 이를 ALREADY_TRIED
에 포함시킨다.
"""
import json
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db, explore, runs


class _FakeLLM:
    model = "fake-reject"

    def __init__(self, explore_replies):
        self._replies = list(explore_replies)

    def structured_chat(self, messages, schema, purpose):
        if purpose == "bootstrap_probe":
            return {"tool": "", "target_id": "", "args": {}, "rationale": ""}
        return self._replies.pop(0)


_BASE_PROPOSAL = {"rationale": "테스트", "expected_evidence": "x", "stop_condition": "x",
                  "based_on_evidence_ids": []}


class RejectedProposalMemory(unittest.TestCase):
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

    def _run_and_ctx(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-rejected-memory",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        return run, ctx

    def test_unregistered_tool_rejection_recorded(self):
        run, ctx = self._run_and_ctx()
        bad = {**_BASE_PROPOSAL, "target_id": "baseline-demo", "tool": "no.such.tool", "args": {}, "risk": "read"}
        ctx.llm = _FakeLLM([{"proposals": [bad]}])
        accepted = explore.propose(ctx)
        self.assertEqual(accepted, 0)
        rejected = self.conn.execute(
            "SELECT payload FROM exploration_nodes WHERE run_id=%s AND kind='intent' AND state='REJECTED'",
            (run["id"],)).fetchall()
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["payload"]["tool"], "no.such.tool")
        self.assertIn("등록되지", rejected[0]["payload"]["rejected_reason"])

    def test_rejected_proposal_appears_in_next_context(self):
        run, ctx = self._run_and_ctx()
        bad = {**_BASE_PROPOSAL, "target_id": "baseline-demo", "tool": "no.such.tool", "args": {}, "risk": "read"}
        ctx.llm = _FakeLLM([{"proposals": [bad]}])
        explore.propose(ctx)
        text, _known, _latest = explore._context(ctx)
        self.assertIn("no.such.tool", text)
        self.assertIn("ALREADY_TRIED", text)

    def test_out_of_scope_target_rejection_recorded_without_asset_anchor(self):
        run, ctx = self._run_and_ctx()
        bad = {**_BASE_PROPOSAL, "target_id": "nonexistent-target", "tool": "fs.list_dir", "args": {}, "risk": "read"}
        ctx.llm = _FakeLLM([{"proposals": [bad]}])
        accepted = explore.propose(ctx)
        self.assertEqual(accepted, 0)
        rejected = self.conn.execute(
            "SELECT payload FROM exploration_nodes WHERE run_id=%s AND kind='intent' AND state='REJECTED'",
            (run["id"],)).fetchall()
        self.assertEqual(len(rejected), 1)
        self.assertIn("범위에 없는", rejected[0]["payload"]["rejected_reason"])


if __name__ == "__main__":
    unittest.main()
