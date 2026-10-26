"""Worker 에이전트화(멀티에이전트 1단계)를 확인한다.

Planner(explore.propose)가 고른 tool/args 는 '제안'일 뿐이고, Worker(worker._worker_decide)가 같은 대상·read
위험도 안에서 다시 고를 수 있다. 무엇을 고르든 worker.run_tool() 하나만 거쳐 정책 검사를 다시 받으므로
(CLAUDE.md §7/§8) 실행 권한의 경계는 바뀌지 않는다. write 제안과 재검증(retest) 작업은 사람의 승인/원래
신호와의 대조가 정확한 tool+args 에 묶여 있어 재판단하지 않고 Planner 제안을 그대로 써야 한다.
"""
import json
import unittest

from psycopg.types.json import Jsonb

from audit.models import ConfigError, ToolOutput, ToolSpec
from diagnostic_workbench import config, db, runs, worker


class _FakeLLM:
    model = "fake-worker-agent"

    def __init__(self, response=None, fail_if_called=False):
        self._response = response
        self._fail = fail_if_called
        self.calls = 0

    def structured_chat(self, messages, schema, purpose):
        self.calls += 1
        if self._fail:
            raise AssertionError("이 작업은 Worker 에이전트가 LLM 을 호출하면 안 됨")
        return self._response


class WorkerAgentDecideTest(unittest.TestCase):
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

    def _run_and_item(self, suggested_tool="test.probe", suggested_risk="read"):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-worker-agent",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        for name, risk in (("test.probe", suggested_risk), ("test.probe2", "read")):
            ctx.registry[name] = ToolSpec(name=name, risk=risk, args_schema={}, scope_args={},
                                          executor=lambda args, n=name: ToolOutput(f'{{"from":"{n}"}}'.encode(), f"{n}/1"))
        asset_id = next(iter(ctx.targets))
        node = self.conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'intent','test',%s) "
            "RETURNING id", (run["id"], Jsonb({"rationale": "테스트 의도", "expected_evidence": "테스트"}))).fetchone()["id"]
        item_id = self.conn.execute(
            "INSERT INTO backlog_items (run_id, kind, exploration_node_id, asset_id, priority, idempotency_key, "
            "reason, payload, max_attempts, status) VALUES (%s,'exploration',%s,%s,80,%s,'테스트',%s,1,'RUNNING') "
            "RETURNING id", (run["id"], node, asset_id, f"test-{node}", Jsonb({"tool": suggested_tool, "args": {}}))
        ).fetchone()["id"]
        item = dict(self.conn.execute("SELECT * FROM backlog_items WHERE id=%s", (item_id,)).fetchone())
        return ctx, worker.load_item(ctx, item)

    def test_confirms_suggestion_when_llm_agrees(self):
        ctx, it = self._run_and_item()
        ctx.llm = _FakeLLM({"tool": "test.probe", "args": {}, "rationale": "제안 그대로"})
        tool, args = worker._worker_decide(ctx, it)
        self.assertEqual(tool, "test.probe")
        self.assertEqual(ctx.llm.calls, 1)

    def test_overrides_to_a_different_read_tool(self):
        ctx, it = self._run_and_item()
        ctx.llm = _FakeLLM({"tool": "test.probe2", "args": {}, "rationale": "더 나은 선택"})
        tool, args = worker._worker_decide(ctx, it)
        self.assertEqual(tool, "test.probe2")
        ev = self.conn.execute(
            "SELECT details FROM audit_events WHERE run_id=%s AND event='worker_agent_decided'",
            (ctx.run["id"],)).fetchone()
        self.assertIsNotNone(ev)
        self.assertEqual(ev["details"]["picked"]["tool"], "test.probe2")

    def test_write_risk_suggestion_is_never_reconsidered(self):
        ctx, it = self._run_and_item(suggested_risk="write")
        ctx.llm = _FakeLLM(fail_if_called=True)  # 승인은 제안의 정확한 tool+args 해시에 묶여 있어 재판단 금지
        tool, args = worker._worker_decide(ctx, it)
        self.assertEqual(tool, "test.probe")
        self.assertEqual(ctx.llm.calls, 0)

    def test_retest_item_is_never_reconsidered(self):
        ctx, it = self._run_and_item()
        self.conn.execute("UPDATE exploration_nodes SET payload = payload || %s WHERE id=%s",
                          (Jsonb({"retest_of_finding": "00000000-0000-0000-0000-000000000000"}),
                           it["exploration_node_id"]))
        ctx.llm = _FakeLLM(fail_if_called=True)  # 재검증은 원래 호출과 똑같아야 신호 대조가 유효함
        tool, args = worker._worker_decide(ctx, it)
        self.assertEqual(tool, "test.probe")
        self.assertEqual(ctx.llm.calls, 0)

    def test_invalid_llm_pick_falls_back_to_suggestion(self):
        ctx, it = self._run_and_item()
        ctx.llm = _FakeLLM({"tool": "nonexistent.tool", "args": {}, "rationale": "엉뚱함"})
        tool, args = worker._worker_decide(ctx, it)
        self.assertEqual(tool, "test.probe")  # CLAUDE.md §7: 엉뚱한 응답에도 Planner 제안으로 정상 유지


if __name__ == "__main__":
    unittest.main()
