import contextlib
import io
import json
import unittest

from audit.cli import main
from tests.helpers import PLAN, Home


class CLITest(unittest.TestCase):
    def setUp(self):
        self.h = Home()
        self.home = str(self.h.path)
        self.plan = self.h.path / "plan.json"
        self.plan.write_text(json.dumps(PLAN), encoding="utf-8")

    def tearDown(self):
        self.h.close()

    def cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                rc = main(["--home", self.home, *argv])
            except SystemExit as e:  # argparse 사용법 오류
                rc = e.code
        return rc, out.getvalue()

    def test_happy_path_exit_codes(self):
        self.assertEqual(self.cli("checklist", "validate")[0], 0)
        self.assertEqual(self.cli("plan", str(self.plan))[0], 0)
        self.assertEqual(self.cli("run", str(self.plan), "--dry-run")[0], 0)
        rc, out = self.cli("run", str(self.plan))
        self.assertEqual(rc, 0)
        run_id = out.split()[1]
        self.assertEqual(self.cli("results", run_id)[0], 0)
        self.assertEqual(self.cli("report", run_id)[0], 0)
        self.assertEqual(self.cli("mcp", "list")[0], 0)

    def test_failure_exit_codes(self):
        bad = self.h.path / "bad.json"
        bad.write_text(json.dumps({**PLAN, "targets": ["evil"]}), encoding="utf-8")
        self.assertEqual(self.cli("plan", str(bad))[0], 1)
        self.assertEqual(self.cli("run", str(bad))[0], 1)
        self.assertEqual(self.cli("results", "NOPE")[0], 1)
        self.assertEqual(self.cli("report", "NOPE")[0], 1)
        self.assertEqual(self.cli("nonsense")[0], 2)
        (self.h.path / "config/scope.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.cli("plan", str(self.plan))[0], 1)  # scope 가 깨지면 fail-closed


if __name__ == "__main__":
    unittest.main()
