"""interactive_shell(F3b-2)을 확인한다.

- shell.open_session/send/close_session 이 risk="dangerous" 로 등록돼 있다(LLM 자율 제안에서 항상
  제외되는 위험도 — explore.py 의 offered_risks 가 보장, 이 테스트는 등록만 확인).
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

    def test_registered_as_dangerous(self):
        for name in ("shell.open_session", "shell.send", "shell.close_session"):
            self.assertIn(name, self.tools)
            self.assertEqual(self.tools[name].risk, "dangerous")

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


class ShellToolsExcludedFromAutonomousProposalDB(unittest.TestCase):
    """CLAUDE.md §8-7 의 핵심 보장: LLM 은 셸 도구를 자율 제안받을 수 없다(risk="dangerous" 가
    explore.py 의 offered_risks={"read","write"} 에서 항상 빠지므로) — 실제 플래너 프롬프트 텍스트까지
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

    def test_shell_tools_never_appear_in_planner_prompt(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-shell-exclude",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        self.assertIn("shell.open_session", ctx.registry)  # 등록은 돼 있다(collect 로는 쓸 수 있어야 함)
        text, _known, _latest = explore._context(ctx)
        self.assertNotIn("shell.open_session", text)
        self.assertNotIn("shell.send", text)
        self.assertNotIn("shell.close_session", text)


if __name__ == "__main__":
    unittest.main()
