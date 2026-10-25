"""DB 로 편집 가능한 에이전트 프롬프트를 확인한다.

- 오버라이드가 없으면 코드 기본값이 그대로 나온다(동작 변경 없음).
- set 하면 그 값이 나오고, reset 하면 다시 기본값으로 돌아간다.
- explore.propose 가 실제로 DB 오버라이드를 읽어 LLM 에 보내는 system 메시지로 쓴다.
"""
import json
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db, explore, prompts, runs


class _FakeLLM:
    model = "fake-prompt"

    def __init__(self, explore_replies):
        self._replies = list(explore_replies)
        self.last_system = None

    def structured_chat(self, messages, schema, purpose):
        if purpose == "bootstrap_probe":
            return {"tool": "", "target_id": "", "args": {}, "rationale": ""}
        self.last_system = messages[0]["content"]
        return self._replies.pop(0)


class AgentPromptsDB(unittest.TestCase):
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

    def test_default_when_no_override(self):
        self.assertEqual(prompts.get(self.conn, "no-such-name", "기본값"), "기본값")

    def test_set_then_get_then_reset(self):
        prompts.set(self.conn, "test_prompt_unit", "바뀐 값", operator="tester")
        self.addCleanup(prompts.reset, self.conn, "test_prompt_unit")
        self.assertEqual(prompts.get(self.conn, "test_prompt_unit", "기본값"), "바뀐 값")
        prompts.reset(self.conn, "test_prompt_unit")
        self.assertEqual(prompts.get(self.conn, "test_prompt_unit", "기본값"), "기본값")

    def test_explore_propose_uses_db_override_as_system_message(self):
        prompts.set(self.conn, "explore_system", "커스텀 탐색 지시문", operator="tester")
        self.addCleanup(prompts.reset, self.conn, "explore_system")
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-agent-prompt",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        ctx.llm = _FakeLLM([{"proposals": []}])
        explore.propose(ctx)
        self.assertEqual(ctx.llm.last_system, "커스텀 탐색 지시문")


if __name__ == "__main__":
    unittest.main()
