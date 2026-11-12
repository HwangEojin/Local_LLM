"""interactive_shell(F3b-2)을 확인한다.

- shell.open_session/send/close_session 이 read_only=False 로 등록돼 있다. 위험도 분류 삭제(2026-11)
  이후로는 이 때문에 LLM 자율 제안에서 빠지지 않는다 — 대신 config/intercept_rules.json 의
  ask-dangerous-shell 규칙이 모든 shell.* 호출에 사람 승인을 강제한다(아래 두 번째 클래스에서 확인).
- 열기 -> 명령 전송 -> 출력 확인 -> 닫기가 실제로 동작한다(진짜 PTY 가 아니라 파이프 기반, ponytail 주석대로).
- Kill Switch(stop_file)가 켜져 있으면 새 세션을 열 수 없다.
- 닫힌(또는 없는) session_id 로 send 하면 ToolError.
"""
import json
import unittest

from audit.models import ConfigError, ToolError
from diagnostic_workbench import config, db, explore, runs, tools


class ShellToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = config.load(None)
        cls.tools = tools.make_tools(cls.cfg)

    def test_registered_as_not_read_only(self):
        for name in ("shell.open_session", "shell.send", "shell.close_session"):
            self.assertIn(name, self.tools)
            self.assertFalse(self.tools[name].read_only)

    def test_open_send_close_roundtrip(self):
        out = self.tools["shell.open_session"].executor({"host": "localhost"})
        session_id = json.loads(out.data)["session_id"]
        try:
            sent = self.tools["shell.send"].executor({"session_id": session_id, "command": "echo marker-xyz",
                                                       "wait_s": 1.5})
            self.assertIn(b"marker-xyz", sent.data)
        finally:
            closed = self.tools["shell.close_session"].executor({"session_id": session_id})
            self.assertEqual(closed.data, b"{}")

    def test_send_after_close_is_tool_error(self):
        out = self.tools["shell.open_session"].executor({"host": "localhost"})
        session_id = json.loads(out.data)["session_id"]
        self.tools["shell.close_session"].executor({"session_id": session_id})
        with self.assertRaises(ToolError):
            self.tools["shell.send"].executor({"session_id": session_id, "command": "echo x"})

    def test_send_unknown_session_is_tool_error(self):
        with self.assertRaises(ToolError):
            self.tools["shell.send"].executor({"session_id": "no-such-session", "command": "echo x"})

    def test_kill_switch_blocks_new_session(self):
        self.cfg.stop_file.write_text("test")
        try:
            with self.assertRaises(ToolError):
                self.tools["shell.open_session"].executor({"host": "localhost"})
        finally:
            self.cfg.stop_file.unlink()


class ShellToolsRequireApprovalDB(unittest.TestCase):
    """위험도 분류 삭제(2026-11) 이후 CLAUDE.md §8-7 의 핵심 보장이 바뀐 자리: LLM 은 이제 셸 도구를
    자율 제안할 수 있지만(아래 첫 테스트), 실제 호출은 config/intercept_rules.json 의 ask-dangerous-shell
    규칙이 모든 shell.* 에 사람 승인을 강제한다(두 번째 테스트) — 제안 가능과 승인 요구는 별개임을
    끝까지 확인한다(단위 등록만이 아니라)."""

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

    def test_shell_tools_appear_in_planner_prompt(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-shell-proposable",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        self.assertIn("shell.open_session", ctx.registry)
        text, _known, _latest = explore._context(ctx)
        self.assertIn("shell.open_session", text)
        self.assertIn("shell.send", text)
        self.assertIn("shell.close_session", text)

    def test_shell_tool_calls_still_require_intercept_approval(self):
        from diagnostic_workbench import policy as wb_policy
        for name in ("shell.open_session", "shell.send", "shell.close_session"):
            self.assertIsNotNone(wb_policy._intercepted(self.cfg.intercept_rules, name, {}),
                                 f"{name} 에 걸리는 인터셉트 규칙이 없음 — 승인 없이 바로 실행됨")


if __name__ == "__main__":
    unittest.main()
