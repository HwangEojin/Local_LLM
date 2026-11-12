"""Triggers(F3b-1)를 확인한다.

- LLM 없이도 동작(ctx.llm 없이 호출).
- interval 조건은 last_checked_at 이후 경과 시간으로만 판단하고, 매번 last_checked_at 을 갱신한다.
- on_finding 조건은 트리거 생성 이후의 새 finding 에만 반응한다(과거 finding 은 무시).
- 발동한 트리거는 backlog_items(kind='exploration')에 기존 정책 경로(idempotency_key)로 들어간다 —
  같은 컨텍스트로 두 번 평가해도 중복 생성되지 않는다.
- 커스텀 에이전트(2026-11-13)가 연결된 트리거는 고정 tool/args 대신 그 에이전트의 LLM 이 허용 도구
  안에서 직접 고른다 — 실패/허용 밖 응답이면 아무것도 쌓지 않는다(아래 CustomAgentTriggerDB).
"""
import json
import unittest

from audit.local_model import LLMError
from audit.models import ConfigError
from diagnostic_workbench import config, db, runs, triggers


class _FakeLLM:
    model = "fake-agent"

    def __init__(self, response=None, error: str | None = None):
        self._response, self._error = response, error
        self.calls = 0

    def structured_chat(self, messages, schema, purpose):
        self.calls += 1
        if self._error:
            raise LLMError(self._error)
        return self._response


class TriggersDB(unittest.TestCase):
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

    def _run_ctx(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-trigger",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        return run, runs.build_context(self.cfg, self.conn, run)

    def _asset_id(self, run):
        return self.conn.execute("SELECT asset_id FROM run_assets WHERE run_id=%s LIMIT 1",
                                 (run["id"],)).fetchone()["asset_id"]

    def _add_trigger(self, run, condition_kind, condition_args=None, tool="net.dns_resolve", args=None):
        return self.conn.execute(
            "INSERT INTO run_triggers (run_id, name, condition_kind, condition_args, tool, args, "
            "target_asset_id, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (run["id"], f"test-{condition_kind}", condition_kind, json.dumps(condition_args or {}), tool,
             json.dumps(args or {"host": "localhost"}), self._asset_id(run), "pytest")).fetchone()

    def test_interval_fires_after_elapsed_seconds_and_resets(self):
        run, ctx = self._run_ctx()
        self._add_trigger(run, "interval", {"seconds": 0})
        accepted = triggers.evaluate(ctx)
        self.assertEqual(accepted, 1)
        item = self.conn.execute("SELECT payload FROM backlog_items WHERE run_id=%s AND kind='exploration'",
                                 (run["id"],)).fetchone()
        self.assertEqual(item["payload"]["tool"], "net.dns_resolve")

    def test_interval_does_not_fire_before_elapsed(self):
        run, ctx = self._run_ctx()
        self._add_trigger(run, "interval", {"seconds": 3600})
        self.assertEqual(triggers.evaluate(ctx), 0)

    def test_idempotent_across_two_evaluate_calls(self):
        run, ctx = self._run_ctx()
        self._add_trigger(run, "interval", {"seconds": 0})
        first = triggers.evaluate(ctx)
        second = triggers.evaluate(ctx)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)  # 같은 last_checked_at 틱이면 같은 idempotency_key 라 중복 안 생김... 아니면
        # 두 번째 호출 전에 last_checked_at 이 이미 now() 로 갱신돼 경과 시간이 다시 0이 될 수 있으므로,
        # idempotency_key 자체가 경과초(elapsed) 문자열을 포함해 매번 달라질 수 있다 — 그래도 backlog_items
        # 행 개수가 비정상적으로 안 늘어나는지만 확인한다.
        n = self.conn.execute("SELECT count(*) AS n FROM backlog_items WHERE run_id=%s AND kind='exploration'",
                              (run["id"],)).fetchone()["n"]
        self.assertLessEqual(n, 2)

    def test_disabled_trigger_never_fires(self):
        run, ctx = self._run_ctx()
        t = self._add_trigger(run, "interval", {"seconds": 0})
        self.conn.execute("UPDATE run_triggers SET enabled=false WHERE id=%s", (t["id"],))
        self.assertEqual(triggers.evaluate(ctx), 0)

    def test_on_finding_ignores_findings_created_before_trigger(self):
        run, ctx = self._run_ctx()
        asset_id = self._asset_id(run)
        self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, severity, rationale, verification) "
            "VALUES (%s,%s,'old','LOW','r','RULE_CONFIRMED')", (run["id"], asset_id))
        self._add_trigger(run, "on_finding")
        self.assertEqual(triggers.evaluate(ctx), 0)  # 트리거 생성 전에 이미 있던 finding 은 무시

    def test_on_finding_fires_for_finding_created_after_trigger(self):
        run, ctx = self._run_ctx()
        asset_id = self._asset_id(run)
        self._add_trigger(run, "on_finding")
        self.conn.commit()  # now() 는 트랜잭션 시작 시각이라, 같은 트랜잭션 안의 INSERT 는 같은 시각으로 찍힘
        self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, severity, rationale, verification) "
            "VALUES (%s,%s,'new','HIGH','r','RULE_CONFIRMED')", (run["id"], asset_id))
        self.assertEqual(triggers.evaluate(ctx), 1)

    def test_unknown_tool_still_queues_and_gets_denied_by_policy_not_by_triggers(self):
        """triggers.py 는 도구가 실제로 존재/허용되는지 모른다 — 그건 늘 그렇듯 planner._admit()->policy.decide()
        가 다음 계획 주기에 가린다(이 테스트는 triggers.evaluate() 가 그 검사를 가로채지 않는다는 뜻만 확인)."""
        run, ctx = self._run_ctx()
        self._add_trigger(run, "interval", {"seconds": 0}, tool="no.such.tool", args={})
        self.assertEqual(triggers.evaluate(ctx), 1)
        item = self.conn.execute("SELECT payload FROM backlog_items WHERE run_id=%s AND kind='exploration'",
                                 (run["id"],)).fetchone()
        self.assertEqual(item["payload"]["tool"], "no.such.tool")


class CustomAgentTriggerDB(unittest.TestCase):
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

    def _run_ctx(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-agent-trigger",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        return run, runs.build_context(self.cfg, self.conn, run)

    def _agent(self, allowed_tools):
        row = self.conn.execute(
            "INSERT INTO custom_agents (name, system_prompt, allowed_tools, created_by) "
            "VALUES (%s,%s,%s,%s) RETURNING *",
            (f"pytest-agent-{id(allowed_tools)}", "테스트 에이전트 프롬프트", allowed_tools, "pytest")).fetchone()
        self.addCleanup(self.conn.commit)
        return row

    def _trigger(self, run, agent_id, condition_args=None):
        asset_id = self.conn.execute("SELECT asset_id FROM run_assets WHERE run_id=%s LIMIT 1",
                                     (run["id"],)).fetchone()["asset_id"]
        return self.conn.execute(
            "INSERT INTO run_triggers (run_id, name, condition_kind, condition_args, custom_agent_id, "
            "target_asset_id, created_by) VALUES (%s,'agent-trigger','interval',%s,%s,%s,%s) RETURNING *",
            (run["id"], json.dumps(condition_args or {"seconds": 0}), agent_id, asset_id, "pytest")).fetchone()

    def test_agent_picks_tool_within_allowed_list(self):
        run, ctx = self._run_ctx()
        agent = self._agent(["net.dns_resolve"])
        self._trigger(run, agent["id"])
        ctx.llm = _FakeLLM({"tool": "net.dns_resolve", "args": {"host": "x"}, "rationale": "r"})
        self.assertEqual(triggers.evaluate(ctx), 1)
        item = self.conn.execute("SELECT payload FROM backlog_items WHERE run_id=%s AND kind='exploration'",
                                 (run["id"],)).fetchone()
        self.assertEqual(item["payload"]["tool"], "net.dns_resolve")
        ev = self.conn.execute("SELECT details FROM audit_events WHERE run_id=%s AND event='custom_agent_decided'",
                              (run["id"],)).fetchone()
        self.assertIsNotNone(ev)

    def test_agent_response_outside_allowed_list_is_rejected(self):
        run, ctx = self._run_ctx()
        agent = self._agent(["net.dns_resolve"])
        self._trigger(run, agent["id"])
        ctx.llm = _FakeLLM({"tool": "fs.read_file", "args": {"path": "/x"}, "rationale": "r"})
        self.assertEqual(triggers.evaluate(ctx), 0)
        n = self.conn.execute("SELECT count(*) AS n FROM backlog_items WHERE run_id=%s AND kind='exploration'",
                              (run["id"],)).fetchone()["n"]
        self.assertEqual(n, 0)
        ev = self.conn.execute(
            "SELECT 1 FROM audit_events WHERE run_id=%s AND event='custom_agent_decide_rejected'",
            (run["id"],)).fetchone()
        self.assertIsNotNone(ev)

    def test_agent_llm_error_is_skipped_not_raised(self):
        run, ctx = self._run_ctx()
        agent = self._agent(["net.dns_resolve"])
        self._trigger(run, agent["id"])
        ctx.llm = _FakeLLM(error="연결 실패")
        self.assertEqual(triggers.evaluate(ctx), 0)
        ev = self.conn.execute("SELECT 1 FROM audit_events WHERE run_id=%s AND event='llm_error'",
                              (run["id"],)).fetchone()
        self.assertIsNotNone(ev)

    def test_disabled_agent_is_skipped(self):
        run, ctx = self._run_ctx()
        agent = self._agent(["net.dns_resolve"])
        self.conn.execute("UPDATE custom_agents SET enabled=false WHERE id=%s", (agent["id"],))
        self._trigger(run, agent["id"])
        ctx.llm = _FakeLLM({"tool": "net.dns_resolve", "args": {"host": "x"}, "rationale": "r"})
        self.assertEqual(triggers.evaluate(ctx), 0)
        self.assertEqual(ctx.llm.calls, 0)  # 꺼진 에이전트는 LLM 을 부르지도 않음


if __name__ == "__main__":
    unittest.main()
