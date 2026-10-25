"""여러 proposal 사이의 선행관계 자동 생성(ARTEX 공유 todolist 대응)을 확인한다.

LLM 이 같은 응답에서 proposal B 가 proposal A(더 이른 인덱스) 에 의존한다고(depends_on) 표시하면
explore.propose 가 backlog_deps 를 만든다. 게이팅 자체(READY/BLOCKED 전환)는 기존 planner._admit 이
이미 하므로 여기서는 backlog_deps 행이 정확히 생기는지만 확인한다.
"""
import json
import unittest

from audit.models import ConfigError, ToolOutput, ToolSpec
from diagnostic_workbench import config, db, explore, runs


class _FakeLLM:
    model = "fake-dep"

    def __init__(self, explore_replies):
        self._replies = list(explore_replies)

    def structured_chat(self, messages, schema, purpose):
        if purpose == "bootstrap_probe":
            return {"tool": "", "target_id": "", "args": {}, "rationale": ""}
        return self._replies.pop(0)


_BASE = {"rationale": "테스트", "expected_evidence": "x", "stop_condition": "x", "based_on_evidence_ids": []}


class DependencyChain(unittest.TestCase):
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
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-dependency-chain",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        ctx.registry["test.probe"] = ToolSpec(name="test.probe", risk="read", args_schema={}, scope_args={},
                                              executor=lambda args: ToolOutput(b'{"ok":true}', "test.probe/1"))
        return run, ctx

    def test_dependent_proposal_creates_backlog_dep(self):
        run, ctx = self._run_and_ctx()
        step_a = {**_BASE, "target_id": "baseline-demo", "tool": "test.probe", "args": {"a": 1}, "risk": "read"}
        step_b = {**_BASE, "target_id": "baseline-demo", "tool": "test.probe", "args": {"a": 2}, "risk": "read",
                 "depends_on": 0}
        ctx.llm = _FakeLLM([{"proposals": [step_a, step_b]}])
        accepted = explore.propose(ctx)
        self.assertEqual(accepted, 2)
        items = self.conn.execute(
            "SELECT id, payload FROM backlog_items WHERE run_id=%s AND kind='exploration' ORDER BY created_at",
            (run["id"],)).fetchall()
        self.assertEqual(len(items), 2)
        dep = self.conn.execute("SELECT * FROM backlog_deps WHERE item_id=%s", (items[1]["id"],)).fetchone()
        self.assertIsNotNone(dep)
        self.assertEqual(dep["depends_on_id"], items[0]["id"])

    def test_no_depends_on_creates_no_dep_row(self):
        run, ctx = self._run_and_ctx()
        step = {**_BASE, "target_id": "baseline-demo", "tool": "test.probe", "args": {"a": 3}, "risk": "read"}
        ctx.llm = _FakeLLM([{"proposals": [step]}])
        explore.propose(ctx)
        item = self.conn.execute(
            "SELECT id FROM backlog_items WHERE run_id=%s AND kind='exploration'", (run["id"],)).fetchone()
        self.assertIsNone(self.conn.execute("SELECT * FROM backlog_deps WHERE item_id=%s", (item["id"],)).fetchone())


if __name__ == "__main__":
    unittest.main()
