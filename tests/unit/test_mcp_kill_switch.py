"""MCP 호출도 Kill Switch 로 진행 중에 취소되는지 확인한다(CLAUDE.md §8 Kill Switch 동작, 2026-10-19 —
신규 디스패치만 막던 기존 동작을 고쳤다). 실제 MCP 서버 없이 audit.mcp_client._run 의 취소 메커니즘만
직접 확인한다(asyncio.run() 은 외부에서 취소할 방법이 없었던 지점).
"""
import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path

from audit import mcp_client
from audit.models import ToolError


class McpRunCancelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stop_file = Path(self.tmp.name) / "STOP"

    def tearDown(self):
        self.tmp.cleanup()

    def test_normal_call_completes(self):
        async def coro():
            await asyncio.sleep(0.05)
            return "done"
        self.assertEqual(mcp_client._run(coro, {"timeout_s": 5}, self.stop_file), "done")

    def test_stop_file_cancels_in_flight_call(self):
        start = time.monotonic()

        def trigger():
            time.sleep(0.4)
            self.stop_file.touch()
        threading.Thread(target=trigger, daemon=True).start()

        async def coro():
            await asyncio.sleep(30)
            return "should not get here"
        with self.assertRaises(ToolError) as cm:
            mcp_client._run(coro, {"timeout_s": 30}, self.stop_file)
        elapsed = time.monotonic() - start
        self.assertIn("전체 중지 스위치", str(cm.exception))
        self.assertLess(elapsed, 5)  # 30초를 다 기다리지 않고 거의 즉시 취소돼야 함


if __name__ == "__main__":
    unittest.main()
