"""통합 테스트: tests/fixtures/test_mcp_server.py 에 대한 실제 MCP stdio 연결."""
import json
import sys
import unittest
from pathlib import Path

from audit import mcp_client
from audit.adapters import build_registry
from audit.models import PolicyDenied, Target, ToolError
from tests.helpers import Home

SERVER = str(Path(__file__).resolve().parents[1] / "fixtures" / "test_mcp_server.py")


def srv(**over):
    return {"enabled": True, "transport": "stdio", "command": sys.executable, "args": [SERVER],
            "timeout_s": 20, "allowed_tools": {
                "echo": {"risk": "read", "args_schema": {"type": "object", "additionalProperties": False,
                                                         "required": ["text"],
                                                         "properties": {"text": {"type": "string",
                                                                                 "maxLength": 100}}}},
                "injected": {"risk": "read"},
                "slow": {"risk": "read"}}, **over}


class MCPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = Home()
        cls.reg = build_registry(cls.h.settings, {"t": srv()})
        cls.target = cls.h.policy.target("sample-app")

    @classmethod
    def tearDownClass(cls):
        cls.h.close()

    def test_list_tools(self):
        info = mcp_client.list_tools(srv())
        self.assertIn("audit-test-server", info["server"])
        self.assertIn("echo", [t["name"] for t in info["tools"]])

    def test_allowed_call(self):
        spec = self.reg["mcp:t/echo"]
        args = self.h.policy.authorize(spec, spec.name, {"text": "hi"}, self.target)
        out = spec.executor(args)
        self.assertEqual(json.loads(out.data)["content"][0]["text"], "hi")
        self.assertTrue(out.tool_version.startswith("audit-test-server/"))

    def test_not_allowlisted_tool_blocked(self):
        self.assertNotIn("mcp:t/delete_everything", self.reg)
        with self.assertRaises(PolicyDenied):
            self.h.policy.authorize(self.reg.get("mcp:t/delete_everything"), "mcp:t/delete_everything",
                                    {}, self.target)

    def test_bad_args_rejected(self):
        spec = self.reg["mcp:t/echo"]
        for bad in ({"text": 1}, {}, {"text": "x" * 101}, {"text": "a", "cmd": "x"}):
            with self.assertRaises(PolicyDenied, msg=bad):
                self.h.policy.authorize(spec, spec.name, bad, self.target)
        # 서버 측 스키마도 적용된다(여기의 설정 스키마는 느슨함)
        with self.assertRaises(ToolError):
            mcp_client.call_tool(srv(), "slow", {"seconds": "abc"}, 1000)

    def test_timeout_recorded_as_tool_error(self):
        with self.assertRaises(ToolError) as cm:
            mcp_client.call_tool(srv(timeout_s=3), "slow", {"seconds": 10}, 1000)
        self.assertIn("시간 초과", str(cm.exception))

    def test_connection_failure(self):
        with self.assertRaises(ToolError):
            mcp_client.call_tool(srv(command=sys.executable, args=["-c", "import sys; sys.exit(3)"]),
                                 "echo", {"text": "x"}, 1000)
        with self.assertRaises(ToolError):
            mcp_client.call_tool(srv(command="definitely-not-a-binary-xyz"), "echo", {"text": "x"}, 1000)

    def test_result_is_data_not_instructions(self):
        spec = self.reg["mcp:t/injected"]
        out = spec.executor({})
        eid = self.h.evidence.record("RUN-X", "C", Target("sample-app", "path", "x"), "r",
                                     spec.name, {}, output=out)
        # 증거로 원문 그대로 저장될 뿐 아무것도 실행되지 않는다(delete_everything 은 등록조차 되지 않음)
        self.assertIn(b"IGNORE PREVIOUS INSTRUCTIONS", self.h.evidence.load(eid))


if __name__ == "__main__":
    unittest.main()
