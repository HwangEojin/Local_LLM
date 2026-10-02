"""통합: 연결을 유지하는(persistent) MCP 서버 — 상태가 호출 사이에 이어지는지, 끝나면 정리되는지."""
import json
import sys
import unittest
from pathlib import Path

from audit import mcp_client
from audit.models import ToolError

SERVER = str(Path(__file__).resolve().parents[1] / "fixtures" / "stateful_server.py")


def srv(**over):
    return {"enabled": True, "transport": "stdio", "command": sys.executable, "args": [SERVER], "timeout_s": 30,
            "allowed_tools": {}, **over}


def text(out):
    return json.loads(out.data)["content"][0]["text"]


class PersistentTest(unittest.TestCase):
    def tearDown(self):
        mcp_client.close_all()

    def test_stateless_mode_loses_state_between_calls(self):
        s = srv()
        sid = text(mcp_client.call_tool(s, "open_session", {"target": "app"}, 10_000))
        with self.assertRaises(ToolError):  # 호출마다 새 서버라서 세션이 없다
            mcp_client.call_tool(s, "use_session", {"session_id": sid}, 10_000)
        self.assertNotEqual(text(mcp_client.call_tool(s, "server_pid", {}, 1000)),
                            text(mcp_client.call_tool(s, "server_pid", {}, 1000)))

    def test_persistent_mode_keeps_state_and_process(self):
        s = srv(persistent=True)
        sid = text(mcp_client.call_tool(s, "open_session", {"target": "app"}, 10_000))
        self.assertEqual(text(mcp_client.call_tool(s, "use_session", {"session_id": sid}, 10_000)), "ok:app")
        self.assertEqual(text(mcp_client.call_tool(s, "server_pid", {}, 1000)),
                         text(mcp_client.call_tool(s, "server_pid", {}, 1000)))

    def test_persistent_still_validates_args_and_reports_errors(self):
        s = srv(persistent=True)
        with self.assertRaises(ToolError):
            mcp_client.call_tool(s, "use_session", {"session_id": 123}, 1000)  # 서버 스키마 위반
        with self.assertRaises(ToolError):
            mcp_client.call_tool(s, "nope", {}, 1000)  # 서버가 제공하지 않는 도구
        with self.assertRaises(ToolError):
            mcp_client.call_tool(s, "use_session", {"session_id": "S99"}, 1000)  # 도구가 오류를 반환

    def test_persistent_timeout_is_tool_error(self):
        s = srv(persistent=True, timeout_s=2)
        with self.assertRaises(ToolError) as cm:
            mcp_client.call_tool(s, "slow", {"seconds": 6}, 1000)
        self.assertIn("시간 초과", str(cm.exception))

    def test_close_all_stops_servers_and_reconnects_on_demand(self):
        s = srv(persistent=True)
        pid1 = text(mcp_client.call_tool(s, "server_pid", {}, 1000))
        conn = next(iter(mcp_client._POOL.values()))
        mcp_client.close_all()
        self.assertFalse(conn.thread.is_alive())
        self.assertEqual(mcp_client._POOL, {})
        pid2 = text(mcp_client.call_tool(s, "server_pid", {}, 1000))  # 필요하면 다시 연결
        self.assertNotEqual(pid1, pid2)

    def test_connection_failure_is_tool_error(self):
        with self.assertRaises(ToolError):
            mcp_client.call_tool(srv(persistent=True, command="definitely-not-a-binary-xyz"), "x", {}, 1000)

    def test_error_text_without_error_flag_is_a_failure(self):
        """Ghidra/JADX 브리지처럼 오류를 본문 문구로 돌려주는 서버의 응답은 증거로 쓰지 않는다."""
        pats = [r"^\s*Request failed:", r'^\s*\{\s*"error"\s*:']
        for persistent in (False, True):
            s = srv(persistent=persistent, error_patterns=pats)
            with self.assertRaises(ToolError) as cm:
                mcp_client.call_tool(s, "bridge_down", {}, 10_000)
            self.assertIn("오류 응답", str(cm.exception))
            with self.assertRaises(ToolError):
                mcp_client.call_tool(s, "json_error", {}, 10_000)
            # 패턴에 걸리지 않는 정상 응답은 그대로 통과한다
            self.assertEqual(text(mcp_client.call_tool(s, "open_session", {"target": "x"}, 10_000))[0], "S")
            mcp_client.close_all()
        # 패턴을 지정하지 않으면 기존처럼 본문을 그대로 돌려준다(서버별 설정으로만 켜진다)
        self.assertIn("Request failed", text(mcp_client.call_tool(srv(), "bridge_down", {}, 10_000)))


if __name__ == "__main__":
    unittest.main()
