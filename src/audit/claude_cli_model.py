"""임시 테스트용: 로컬 Ollama 대신 설치된 `claude` CLI(Claude Code, 구독 로그인)를 호출해 구조화
된 탐색 제안을 받는다. API 키 과금이 아니라 이 PC 에 로그인된 Claude 구독으로 돌아간다.

`audit.local_model.OllamaClient` 와 같은 인터페이스(.model, check_ready(), structured_chat())만
맞춘 대체 클라이언트다 — explore.py 의 나머지 로직은 이걸 몰라도 된다. `claude` CLI 는 `--tools ""`
로 띄워 어떤 도구도(Bash, 파일 편집, MCP 등) 못 쓰게 막는다 — 실제 도구 실행은 여전히 Python
정책 계층(policy.decide)만 거치게 하기 위해서다(CLAUDE.md §8-1: 모든 도구 호출은 단일 정책 경로).

WORKBENCH_LLM_PROVIDER=claude_cli 환경 변수로만 켜진다(기본은 그대로 Ollama). 운영 배포용이
아니라 프레임워크 자체가 돌아가는지 확인하기 위한 임시 경로다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time

from . import schemas
from .models import AuditError


class LLMError(AuditError):
    pass


_SCHEMA_INSTRUCTIONS = (
    "Reply with a single JSON object only — no markdown fences, no explanation before or after. "
    "The JSON must validate against this JSON Schema:\n"
)


class ClaudeCliClient:
    def __init__(self, cfg: dict, storage=None, run_id=None):
        self.model = cfg.get("model") or "claude-sonnet-4-5"
        self.timeout_s = cfg.get("timeout_s", 120)
        self.max_calls_per_run = cfg.get("max_calls_per_run", 200)
        self.max_retries = cfg.get("max_retries", 2)
        self.storage = storage
        self.run_id = run_id

    def check_ready(self) -> dict:
        exe = shutil.which("claude")
        if not exe:
            raise LLMError("'claude' CLI 를 PATH 에서 찾을 수 없음 — Claude Code 설치 필요")
        try:
            out = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=15,
                                 encoding="utf-8", errors="replace")
            status = json.loads(out.stdout or "")
        except (subprocess.SubprocessError, json.JSONDecodeError, TypeError, OSError) as e:
            raise LLMError(f"'claude auth status' 확인 실패: {e}") from None
        if not status.get("loggedIn"):
            raise LLMError("claude CLI 가 로그인되어 있지 않음 — 터미널에서 'claude login' 을 먼저 실행하세요")
        return {"model": self.model, "auth": status.get("authMethod")}

    def structured_chat(self, messages: list[dict], schema: dict, purpose: str) -> dict:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        user = "\n".join(m["content"] for m in messages if m["role"] == "user")
        prompt = user + "\n\n" + _SCHEMA_INSTRUCTIONS + json.dumps(schema, ensure_ascii=False)
        retries = self.max_retries
        last = "no attempt"
        for _ in range(retries + 1):
            if self.storage and self.storage.count_model_calls(self.run_id) >= self.max_calls_per_run:
                raise LLMError("max_calls_per_run 한도에 도달함")
            t0 = time.monotonic()
            try:
                text = self._invoke(prompt, system)
            except LLMError as e:
                self._record(purpose, None, t0, False, str(e))
                raise
            try:
                parsed = json.loads(_strip_fences(text))
                errs = schemas.validate(parsed, schema)
            except (TypeError, json.JSONDecodeError) as e:
                errs = [f"모델 출력을 해석할 수 없음: {e}"]
            if errs:
                last = "; ".join(errs)[:500]
                self._record(purpose, None, t0, False, f"출력 형식 오류: {last}")
                continue
            self._record(purpose, None, t0, True)
            return parsed
        raise LLMError(f"모델 출력이 {retries + 1}회 시도 후에도 스키마 검증에 실패함: {last}")

    def _invoke(self, prompt: str, system: str) -> str:
        exe = shutil.which("claude")
        args = [exe, "-p", prompt, "--output-format", "json", "--tools", "", "--model", self.model]
        if system:
            args += ["--system-prompt", system]
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=self.timeout_s,
                                 encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            raise LLMError(f"claude CLI 호출이 {self.timeout_s}초를 넘김") from None
        except OSError as e:
            raise LLMError(f"claude CLI 실행 실패: {e}") from None
        try:
            resp = json.loads(out.stdout or "")
        except (json.JSONDecodeError, TypeError):
            raise LLMError(f"claude CLI 가 JSON 이 아닌 응답을 반환함: "
                           f"{(out.stdout or '')[:300] or (out.stderr or '')[:300]}") from None
        if resp.get("is_error"):
            raise LLMError(f"claude CLI 오류: {resp.get('result')}")
        return resp.get("result", "")

    def _record(self, purpose, resp, t0, ok, error=None):
        if not self.storage:
            return
        self.storage.record_model_call(self.run_id, self.model, purpose, None, None, "unavailable",
                                       int((time.monotonic() - t0) * 1000), ok, error)


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
    return t.strip()
