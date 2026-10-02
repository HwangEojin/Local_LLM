import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from audit.adapters.fs_tools import make_tools
from audit.evidence import EvidenceIntegrityError, EvidenceStore
from audit.models import ConfigError, PolicyDenied, Target, ToolOutput, ToolSpec, Verdict
from audit.policy import Policy, mask_secrets
from audit.storage import Storage
from audit.verdict import decide, evaluate_rule


def scope(targets, risks=("read",), until="2099-01-01"):
    return {"engagement_id": "E1", "approved_by": "t", "valid_until": until,
            "allowed_risks": list(risks), "targets": targets}


def make_link(link: Path, dest: Path) -> bool:
    try:
        os.symlink(dest, link, target_is_directory=dest.is_dir())
        return True
    except OSError:
        pass
    try:  # Windows 정션은 관리자 권한이 필요 없다
        import _winapi
        _winapi.CreateJunction(str(dest), str(link))
        return True
    except (ImportError, OSError):
        return False


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "in"
        self.root.mkdir()
        (self.root / "a.conf").write_text("x")
        self.outside = base / "out"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("s")
        self.policy = Policy(scope([
            {"target_id": "p", "type": "path", "value": str(self.root)},
            {"target_id": "h", "type": "host", "value": "app.test"},
            {"target_id": "u", "type": "url", "value": "https://app.test/api/"},
        ]), base)
        self.tools = make_tools(1024)
        self.t = self.policy.target("p")

    def tearDown(self):
        self.tmp.cleanup()

    def test_allowed_target_and_path_pass(self):
        args = self.policy.authorize(self.tools["fs.read_file"], "fs.read_file",
                                     {"path": str(self.root / "a.conf")}, self.t)
        self.assertEqual(Path(args["path"]), (self.root / "a.conf").resolve())

    def test_unknown_target_denied(self):
        with self.assertRaises(PolicyDenied):
            self.policy.target("nope")

    def test_path_traversal_denied(self):
        for bad in (str(self.root / ".." / "out" / "secret.txt"), str(self.outside / "secret.txt"),
                    "C:/Windows/win.ini"):
            with self.assertRaises(PolicyDenied, msg=bad):
                self.policy.authorize(self.tools["fs.read_file"], "fs.read_file", {"path": bad}, self.t)

    def test_symlink_escape_denied(self):
        link = self.root / "link"
        if not make_link(link, self.outside):
            self.skipTest("cannot create symlink/junction here")
        with self.assertRaises(PolicyDenied):
            self.policy.authorize(self.tools["fs.read_file"], "fs.read_file",
                                  {"path": str(link / "secret.txt")}, self.t)

    def test_host_and_url_scope(self):
        h, u = self.policy.target("h"), self.policy.target("u")
        self.assertEqual(self.policy.check_host("APP.test", h), "APP.test")
        with self.assertRaises(PolicyDenied):
            self.policy.check_host("evil.test", h)
        self.policy.check_url("https://app.test/api/v1", u)
        for bad in ("https://app.test/admin", "http://app.test/api/", "https://evil.test/api/",
                    "file:///etc/passwd"):
            with self.assertRaises(PolicyDenied, msg=bad):
                self.policy.check_url(bad, u)
        with self.assertRaises(PolicyDenied):
            self.policy.check_path(str(self.root), h)  # 호스트 대상에 경로 인자

    def test_unregistered_tool_and_bad_args_denied(self):
        with self.assertRaises(PolicyDenied):
            self.policy.authorize(None, "shell.exec", {"cmd": "whoami"}, self.t)
        with self.assertRaises(PolicyDenied):
            self.policy.authorize(self.tools["fs.read_file"], "fs.read_file", {"path": 1}, self.t)
        with self.assertRaises(PolicyDenied):
            self.policy.authorize(self.tools["fs.read_file"], "fs.read_file",
                                  {"path": "a", "extra": 1}, self.t)

    def test_dangerous_requires_scope_and_approval(self):
        spec = ToolSpec("x.write", "write", {"type": "object"}, {})
        with self.assertRaises(PolicyDenied):  # write 가 allowed_risks 에 없음
            self.policy.authorize(spec, spec.name, {}, self.t, approver=lambda *a: True)
        p2 = Policy(scope([{"target_id": "p", "type": "path", "value": str(self.root)}],
                          risks=("read", "write")), Path(self.tmp.name))
        t = p2.target("p")
        with self.assertRaises(PolicyDenied):  # 승인자 없음
            p2.authorize(spec, spec.name, {}, t)
        with self.assertRaises(PolicyDenied):  # 승인자가 거부
            p2.authorize(spec, spec.name, {}, t, approver=lambda *a: False)
        with self.assertRaises(PolicyDenied):  # True 가 아닌 truthy 값은 승인이 아니다
            p2.authorize(spec, spec.name, {}, t, approver=lambda *a: "yes")
        self.assertEqual(p2.authorize(spec, spec.name, {}, t, approver=lambda *a: True), {})

    def test_bad_scope_fails_closed(self):
        with self.assertRaises(ConfigError):
            Policy(scope([{"target_id": "p", "type": "path", "value": str(self.root)}],
                         until="2000-01-01"), Path("."))
        with self.assertRaises(ConfigError):
            Policy(scope([{"target_id": "p", "type": "path", "value": str(self.root / "missing")}]),
                   Path("."))
        with self.assertRaises(ConfigError):
            Policy(scope([{"target_id": "p", "type": "path", "value": str(self.root)}] * 2), Path("."))

    def test_mask_secrets(self):
        s = mask_secrets('db_password = hunter2\napi_key: "abc123"\nAuthorization: Bearer abcdefghijk')
        for secret in ("hunter2", "abc123", "abcdefghijk"):
            self.assertNotIn(secret, s)


class EvidenceTest(unittest.TestCase):
    def test_hash_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as d:
            st = Storage(Path(d) / "a.db")
            es = EvidenceStore(st, Path(d) / "ev")
            t = Target("p", "path", d)
            eid = es.record("r1", "C1", t, "cfg", "fs.read_file", {"path": "x"},
                            ToolOutput(b"hello", "v1"))
            self.assertEqual(es.load(eid), b"hello")
            row = st.evidence(evidence_id=eid)
            self.assertEqual(len(row["sha256"]), 64)
            Path(row["raw_path"]).write_bytes(b"tampered")
            with self.assertRaises(EvidenceIntegrityError):
                es.load(eid)
            failed = es.record("r1", "C1", t, "cfg", "fs.read_file", {}, error="boom")
            self.assertEqual(st.evidence(evidence_id=failed)["success"], 0)
            st.close()


ITEM = {"item_status": "complete", "verification_method": {"type": "rule"}}
RULE = {"kind": "regex", "evidence": "cfg", "pattern": r"(?im)^debug\s*=\s*true", "fail_if": "match"}


def ev(data, truncated=False):
    return {"cfg": {"evidence_id": "EV-1", "data": data, "truncated": truncated}}


class VerdictTest(unittest.TestCase):
    def test_rule_outcomes(self):
        self.assertEqual(evaluate_rule(RULE, ev(b"debug = true"))["outcome"], "FAIL")
        self.assertEqual(evaluate_rule(RULE, ev(b"debug = false"))["outcome"], "PASS")
        self.assertEqual(evaluate_rule(RULE, ev(b"debug = false", truncated=True))["outcome"],
                         "INCONCLUSIVE")
        self.assertEqual(evaluate_rule(RULE, {})["outcome"], "INCONCLUSIVE")
        self.assertEqual(evaluate_rule(RULE, ev(None))["outcome"], "INCONCLUSIVE")

    def test_missing_evidence_never_pass(self):
        v, _, _ = decide(ITEM, True, False, {"outcome": "PASS"}, {"verdict": "PASS"})
        self.assertEqual(v, Verdict.INCONCLUSIVE)

    def test_llm_disagreement_blocks_auto_confirm(self):
        v, _, _ = decide(ITEM, True, True, {"outcome": "PASS"}, {"verdict": "FAIL"})
        self.assertEqual(v, Verdict.MANUAL_REVIEW)
        v, _, _ = decide(ITEM, True, True, {"outcome": "PASS"}, {"verdict": "ERROR"})
        self.assertEqual(v, Verdict.MANUAL_REVIEW)
        v, _, _ = decide(ITEM, True, True, {"outcome": "FAIL"}, {"verdict": "FAIL"})
        self.assertEqual(v, Verdict.FAIL)

    def test_llm_alone_and_manual_never_confirm(self):
        for method in ("llm_assisted", "manual"):
            item = {"item_status": "complete", "verification_method": {"type": method}}
            v, _, _ = decide(item, True, True, None, {"verdict": "PASS"})
            self.assertEqual(v, Verdict.MANUAL_REVIEW)

    def test_inconclusive_and_incomplete_not_upgraded(self):
        v, _, _ = decide(ITEM, True, True, {"outcome": "INCONCLUSIVE"}, {"verdict": "PASS"})
        self.assertEqual(v, Verdict.INCONCLUSIVE)
        v, _, _ = decide({**ITEM, "item_status": "incomplete"}, True, True, {"outcome": "PASS"}, None)
        self.assertEqual(v, Verdict.MANUAL_REVIEW)
        v, _, _ = decide(ITEM, False, True, {"outcome": "PASS"}, None)
        self.assertEqual(v, Verdict.NOT_APPLICABLE)


if __name__ == "__main__":
    unittest.main()
