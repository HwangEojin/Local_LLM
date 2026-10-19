"""최초 탐색 라운드의 LLM 직접 실행 프로브(CLAUDE.md §1.1 예외 2번, 2026-10-19)를 확인한다.

- bootstrap 라운드에서 LLM이 지시한 read 전용 도구가 백로그 대기 없이 즉시 실행된다(worker.run_tool 재사용).
- 실행 결과(증거)는 같은 라운드의 PROPOSALS 호출 citation 검증에 바로 쓸 수 있게 known 에 들어간다.
- dangerous/write 도구나 범위 밖 대상을 지시하면 실행하지 않고 거부 사유를 남긴다.
- 실행된 프로브는 audit_events(bootstrap_probes)에 기록된다.
"""
import unittest

from audit.models import ConfigError, ToolOutput, ToolSpec
from diagnostic_workbench import config, db, explore, runs


class _SequencedLLM:
    """explore 가 기대하는 최소 인터페이스. 호출 순서대로 미리 정한 응답을 돌려준다."""
    model = "fake-probe"

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def structured_chat(self, messages, schema, purpose):
        self.calls.append((purpose, messages[-1]["content"]))
        return self._responses.pop(0)


class BootstrapProbeTest(unittest.TestCase):
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

    def _run_with_fake_tool(self, risk="read"):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-bootstrap-probe",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        ctx.registry["test.probe"] = ToolSpec(
            name="test.probe", risk=risk, args_schema={}, scope_args={},
            executor=lambda args: ToolOutput(b'{"ok":true}', "test.probe/1"))
        return run, ctx

    def test_probe_executes_immediately_and_feeds_citation(self):
        run, ctx = self._run_with_fake_tool()
        ctx.llm = _SequencedLLM([
            {"tool": "test.probe", "target_id": "baseline-demo", "args": {}, "rationale": "최초 정찰"},
            {"tool": "", "target_id": "", "args": {}, "rationale": ""},
            {"proposals": []},
        ])
        accepted = explore.propose(ctx)
        self.assertEqual(accepted, 0)
        self.assertEqual(len(ctx.llm.calls), 3)
        row = self.conn.execute(
            "SELECT * FROM evidence WHERE run_id=%s AND tool='test.probe'", (run["id"],)).fetchone()
        self.assertIsNotNone(row)
        self.assertTrue(row["success"])
        ev = self.conn.execute(
            "SELECT details FROM audit_events WHERE run_id=%s AND event='bootstrap_probes'",
            (run["id"],)).fetchone()
        self.assertIsNotNone(ev)
        self.assertEqual(ev["details"]["count"], 1)
        # 다음 PROPOSALS 호출에 넘긴 텍스트에 프로브 결과(BOOTSTRAP_PROBES)가 포함돼 인용 가능해야 한다
        self.assertIn("BOOTSTRAP_PROBES", ctx.llm.calls[-1][1])

    def test_dangerous_tool_is_not_executed(self):
        run, ctx = self._run_with_fake_tool(risk="dangerous")
        ctx.llm = _SequencedLLM([
            {"tool": "test.probe", "target_id": "baseline-demo", "args": {}, "rationale": "위험한 시도"},
            {"tool": "", "target_id": "", "args": {}, "rationale": ""},
            {"proposals": []},
        ])
        explore.propose(ctx)
        row = self.conn.execute(
            "SELECT * FROM evidence WHERE run_id=%s AND tool='test.probe'", (run["id"],)).fetchone()
        self.assertIsNone(row)  # dangerous 도구는 직접 실행되지 않음


if __name__ == "__main__":
    unittest.main()
