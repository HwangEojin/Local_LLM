"""실제 Playwright MCP + 헤드리스 브라우저로 검증한다(설치되어 있을 때만 실행).

로컬에 띄운 시험 페이지 하나만 사용하며 외부 네트워크에 접근하지 않는다.
"""
import http.server
import json
import threading
import unittest
from pathlib import Path

from audit import config, mcp_client
from audit.adapters import build_registry
from audit.manual_evidence import add_tool_evidence
from audit.models import PolicyDenied
from audit.policy import Policy
from tests.helpers import PLAN, ROOT, Home

CFG = config.load_mcp_servers(ROOT).get("playwright") if (ROOT / "config" / "mcp_servers.json").exists() else None


def _exe():
    args = (CFG or {}).get("args", [])
    return Path(args[args.index("--executable-path") + 1]) if "--executable-path" in args else None


@unittest.skipUnless(CFG and CFG["enabled"] and _exe() and _exe().exists(),
                     "Playwright MCP 와 브라우저(--executable-path)가 설치되어 있어야 함")
class PlaywrightLiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = "<html><head><title>PW-TEST-PAGE</title></head><body><h1>로컬 시험 페이지</h1></body></html>".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        cls.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}/"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        mcp_client.close_all()

    def setUp(self):
        self.h = Home()
        self.reg = build_registry(self.h.settings, {"playwright": CFG})
        sc = config.load_scope(self.h.path)
        sc["targets"] = [{"target_id": "web", "type": "url", "value": self.url}]
        self.h.policy = Policy(sc, self.h.path)
        self.run_id = self.h.orchestrator().start(self.h.resolve({**PLAN, "targets": ["web"], "check_ids": ["SAMPLE-004"]}))

    def tearDown(self):
        self.h.close()

    def add(self, tool, args):
        return add_tool_evidence(self.h.storage, self.h.evidence, self.h.policy, self.reg, self.h.settings, None,
                                 self.run_id, "SAMPLE-004", "web", f"mcp:playwright/{tool}", args)

    def test_state_persists_between_calls_and_policy_applies(self):
        self.add("browser_navigate", {"url": self.url})
        snap = self.add("browser_snapshot", {})  # 다른 호출이지만 같은 브라우저 페이지
        text = self.h.evidence.load(snap["evidence_id"]).decode("utf-8")
        self.assertIn("PW-TEST-PAGE", text)
        self.assertIn("로컬 시험 페이지", text)
        for tool, args in (("browser_navigate", {"url": "http://evil.example.test/"}),  # 범위 밖 URL
                           ("browser_tabs", {"action": "new", "url": "http://evil.example.test/"}),
                           ("browser_evaluate", {"function": "() => 1"}),  # dangerous
                           ("browser_click", {"target": "e1"}),  # write
                           ("browser_snapshot", {"filename": "x.md"})):  # 로컬 파일 쓰기 인자
            with self.assertRaises(PolicyDenied, msg=tool):
                self.add(tool, args)


if __name__ == "__main__":
    unittest.main()
