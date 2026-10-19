"""Kill Switch 가 진행 중인 서브프로세스도 즉시 종료하는지 확인한다(CLAUDE.md §8 Kill Switch 동작,
2026-10-19 — 신규 디스패치만 막던 기존 동작을 고쳤다).
"""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from audit.models import ToolError
from diagnostic_workbench.tools import _run_cancelable


class RunCancelableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stop_file = Path(self.tmp.name) / "STOP"

    def tearDown(self):
        self.tmp.cleanup()

    def test_normal_completion_returns_output(self):
        p = _run_cancelable(["python3", "-c", "print('hi')"], 5, self.stop_file)
        self.assertEqual(p.returncode, 0)
        self.assertIn(b"hi", p.stdout)

    def test_stop_file_kills_in_flight_process(self):
        start = time.monotonic()

        def trigger():
            time.sleep(0.4)
            self.stop_file.touch()

        threading.Thread(target=trigger, daemon=True).start()
        with self.assertRaises(ToolError) as cm:
            _run_cancelable(["python3", "-c", "import time; time.sleep(30)"], 30, self.stop_file)
        elapsed = time.monotonic() - start
        self.assertIn("전체 중지 스위치", str(cm.exception))
        self.assertLess(elapsed, 5)  # 30초를 다 기다리지 않고 거의 즉시(폴링 간격 수준) 종료돼야 함

    def test_real_timeout_still_raised(self):
        import subprocess
        with self.assertRaises(subprocess.TimeoutExpired):
            _run_cancelable(["python3", "-c", "import time; time.sleep(5)"], 0.3, self.stop_file)


if __name__ == "__main__":
    unittest.main()
