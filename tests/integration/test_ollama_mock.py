"""통합 테스트: 로컬 모의 HTTP 서버에 대한 OllamaClient (실제 모델 없음)."""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from audit import schemas
from audit.local_model import LLMError, OllamaClient
from audit.models import ConfigError
from tests.helpers import Home
from tests.unit.test_checklist_and_run import EXPECTED

STATE = {"chat_replies": [], "requests": []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/version":
            self._send({"version": "0.35.0"})
        elif self.path == "/api/tags":
            self._send({"models": [{"name": "mock-model:latest"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        STATE["requests"].append(req)
        if self.path == "/api/show":
            self._send({"capabilities": ["completion", "tools"]})
        elif self.path == "/api/chat":
            reply = STATE["chat_replies"].pop(0)
            content = reply(req) if callable(reply) else reply
            self._send({"message": {"role": "assistant", "content": content},
                        "prompt_eval_count": 120, "eval_count": 30})


def cfg(port, **over):
    return {"base_url": f"http://127.0.0.1:{port}", "model": "mock-model", "timeout_s": 5,
            "max_request_bytes": 65536, "max_response_bytes": 65536, "max_calls_per_run": 50,
            "max_retries": 1, **over}


def agree_with_evidence(verdict):
    def reply(req):
        import re
        ids = re.findall(r'<evidence id="([^"]+)"', req["messages"][1]["content"])
        return json.dumps({"verdict": verdict, "rationale": "mock", "evidence_ids": ids})
    return reply


class OllamaMockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        STATE["chat_replies"].clear()
        STATE["requests"].clear()
        self.h = Home()

    def tearDown(self):
        self.h.close()

    def test_check_ready(self):
        info = OllamaClient(cfg(self.port)).check_ready()
        self.assertEqual(info["capabilities"], ["completion", "tools"])
        with self.assertRaises(LLMError):
            OllamaClient(cfg(self.port, model="missing")).check_ready()

    def test_remote_endpoint_refused_without_external_api(self):
        with self.assertRaises(ConfigError):
            OllamaClient(cfg(self.port, base_url="https://api.example.com"))

    def test_invalid_output_retried_then_fails_and_is_recorded(self):
        STATE["chat_replies"] += ["not json", '{"verdict": "MAYBE"}']
        c = OllamaClient(cfg(self.port), storage=self.h.storage, run_id="R")
        with self.assertRaises(LLMError):
            c.structured_chat([{"role": "user", "content": "x"}], schemas.LLM_ANALYSIS, "t")
        calls = self.h.storage.model_calls("R")
        self.assertEqual(len(calls), 2)  # 최초 1회 + max_retries, 그 이상은 없음
        self.assertEqual([c["ok"] for c in calls], [0, 0])
        self.assertEqual(calls[0]["token_source"], "reported")
        self.assertEqual(STATE["requests"][0]["format"], schemas.LLM_ANALYSIS)

    def test_call_limit(self):
        c = OllamaClient(cfg(self.port, max_calls_per_run=1), storage=self.h.storage, run_id="R2")
        STATE["chat_replies"].append(json.dumps({"verdict": "PASS", "rationale": "r", "evidence_ids": []}))
        c.structured_chat([{"role": "user", "content": "x"}], schemas.LLM_ANALYSIS, "t")
        with self.assertRaises(LLMError):
            c.structured_chat([{"role": "user", "content": "x"}], schemas.LLM_ANALYSIS, "t")

    def test_full_run_with_llm(self):
        # 규칙 결과: SAMPLE-001 FAIL, -002 FAIL, -003 PASS. LLM은 001/002 에 동의하고 003 에 불일치.
        STATE["chat_replies"] += [agree_with_evidence("FAIL"), agree_with_evidence("FAIL"),
                                  agree_with_evidence("FAIL")]
        llm = OllamaClient(cfg(self.port), storage=self.h.storage)
        orch = self.h.orchestrator(llm=llm)
        llm.run_id = None  # run_id 가 생성되면 아래에서 설정
        resolved = self.h.resolve({**self.h.resolve()["plan"], "use_llm": True})
        orig = orch.execute

        def execute(run_id, r):
            llm.run_id = run_id
            return orig(run_id, r)
        orch.execute = execute
        run_id = orch.start(resolved)
        v = {r["check_id"]: r["verdict"] for r in self.h.storage.results(run_id)}
        self.assertEqual(v, {**EXPECTED, "SAMPLE-003": "MANUAL_REVIEW"})
        self.assertEqual(len(self.h.storage.model_calls(run_id)), 3)
        # 프롬프트가 증거를 신뢰할 수 없는 데이터로 표시한다
        self.assertIn("untrusted", STATE["requests"][-1]["messages"][0]["content"])


@unittest.skipUnless(os.environ.get("AUDIT_LIVE_OLLAMA") == "1", "set AUDIT_LIVE_OLLAMA=1 to test a real Ollama")
class OllamaLiveTest(unittest.TestCase):
    def test_live(self):
        from audit import config
        from tests.helpers import ROOT
        s = config.load_settings(ROOT)
        c = OllamaClient(s.ollama)
        print("live:", c.check_ready())
        out = c.structured_chat([{"role": "user", "content": "Evidence id EV-1 says debug=true. "
                                  "Criteria: FAIL if debug=true. Give verdict."}],
                                schemas.LLM_ANALYSIS, "live-test")
        self.assertEqual(schemas.validate(out, schemas.LLM_ANALYSIS), [])
        print("live output:", out)


if __name__ == "__main__":
    unittest.main()
