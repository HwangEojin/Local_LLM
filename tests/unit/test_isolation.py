"""테스트 격리(tests/unit/__init__.py)가 살아 있는지 지킨다.

테스트가 실제 진단 DB(public 스키마)나 data/evidence 에 쓰기 시작하면 목록에 테스트 진단이 다시 쌓인다.
"""
import os
import unittest
from pathlib import Path

from audit.models import ConfigError
from diagnostic_workbench import config, db

ROOT = Path(__file__).resolve().parents[2]


class IsolationTest(unittest.TestCase):
    def setUp(self):
        if os.environ.get("WORKBENCH_TEST_USE_REAL_DB") == "1":
            self.skipTest("WORKBENCH_TEST_USE_REAL_DB=1: 격리를 일부러 껐음")

    def test_tests_use_dedicated_schema_not_public(self):
        try:
            cfg = config.load(None)
            conn = db.connect(cfg.dsn)
        except ConfigError as e:
            self.skipTest(f"DB 연결 불가: {e}")
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT current_schema() AS s").fetchone()["s"], "workbench_test")

    def test_evidence_dir_is_outside_the_repo(self):
        try:
            cfg = config.load(None)
        except ConfigError as e:
            self.skipTest(f"설정 불가: {e}")
        self.assertNotEqual(cfg.home.resolve(), ROOT.resolve())
        self.assertFalse(str(cfg.evidence_dir.resolve()).startswith(str((ROOT / "data").resolve())))


if __name__ == "__main__":
    unittest.main()
