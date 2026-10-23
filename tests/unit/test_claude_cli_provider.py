"""임시 테스트용 claude CLI LLM 제공자(WORKBENCH_LLM_PROVIDER=claude_cli)를 확인한다.

실제 `claude` CLI 를 부르는 structured_chat 까지는 검증하지 않는다(로그인·네트워크·과금과 무관해야
하는 단위테스트 범위를 벗어남) — check_ready 가 '설치는 됐지만 로그인 안 됨'을 제대로 구분하는지,
그리고 explore.make_llm 이 환경 변수로 Ollama/claude_cli 를 올바르게 고르는지만 확인한다.
"""
import os
import types
import unittest
from unittest import mock

from audit.claude_cli_model import ClaudeCliClient, LLMError, _strip_fences
from diagnostic_workbench import explore


class StripFencesTest(unittest.TestCase):
    def test_plain_json_unchanged(self):
        self.assertEqual(_strip_fences('{"a": 1}'), '{"a": 1}')

    def test_strips_markdown_code_fence(self):
        self.assertEqual(_strip_fences('```json\n{"a": 1}\n```'), '{"a": 1}')

    def test_strips_bare_fence(self):
        self.assertEqual(_strip_fences('```\n{"a": 1}\n```'), '{"a": 1}')


class CheckReadyTest(unittest.TestCase):
    def test_not_logged_in_raises_clear_error(self):
        import shutil
        if not shutil.which("claude"):
            raise unittest.SkipTest("이 환경에 claude CLI 가 없음")
        client = ClaudeCliClient({})
        try:
            client.check_ready()
        except LLMError as e:
            self.assertIn("claude", str(e))  # 메시지가 뭘 설치/로그인해야 하는지 말해줘야 함
        # 로그인돼 있으면 예외 없이 통과 — 둘 다 유효한 결과이므로 별도 단언 없음


class MakeLlmProviderSelectionTest(unittest.TestCase):
    """explore.make_llm 이 환경 변수만으로 Ollama/claude_cli 를 정확히 고르는지, 실제 네트워크/
    서브프로세스 호출 없이(check_ready 를 스텁으로 바꿔) 확인한다.
    """
    def _fake_ctx(self, raw: dict):
        settings = types.SimpleNamespace(ollama={"base_url": "http://127.0.0.1:11434", "model": "x",
                                                  "timeout_s": 1, "max_request_bytes": 1, "max_response_bytes": 1,
                                                  "max_calls_per_run": 1, "max_retries": 0},
                                         external_api_enabled=False, raw=raw)
        cfg = types.SimpleNamespace(settings=settings)
        return types.SimpleNamespace(cfg=cfg, conn=mock.Mock(), run={"id": "test-run"})

    def test_defaults_to_ollama(self):
        os.environ.pop("WORKBENCH_LLM_PROVIDER", None)
        ctx = self._fake_ctx({})
        with mock.patch("audit.local_model.OllamaClient.check_ready", return_value={}):
            llm = explore.make_llm(ctx)
        self.assertEqual(type(llm).__name__, "OllamaClient")

    def test_env_var_selects_claude_cli(self):
        os.environ["WORKBENCH_LLM_PROVIDER"] = "claude_cli"
        self.addCleanup(os.environ.pop, "WORKBENCH_LLM_PROVIDER", None)
        ctx = self._fake_ctx({"claude_cli": {"model": "claude-sonnet-4-5"}})
        with mock.patch("audit.claude_cli_model.ClaudeCliClient.check_ready", return_value={}):
            llm = explore.make_llm(ctx)
        self.assertEqual(type(llm).__name__, "ClaudeCliClient")
        self.assertEqual(llm.model, "claude-sonnet-4-5")


if __name__ == "__main__":
    unittest.main()
