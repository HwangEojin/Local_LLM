"""에이전트 역할별 모델 지정(runs.llm_agent_models)을 확인한다.

- create_run 이 역할별 지정을 검증하고(알 수 없는 역할/프로파일, --llm 없는 지정은 거부) DB 에 고정한다.
- explore.llm_for 는 지정이 없는 역할엔 실행 기본 모델(ctx.llm)을, 있는 역할엔 그 모델 전용 클라이언트를 준다.
- 같은 지정은 클라이언트 하나를 공유하고, ctx.llm 이 없으면(--llm 아님) 지정이 있어도 None 이다.
"""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from audit.local_model import OllamaClient
from audit.models import ConfigError
from diagnostic_workbench import config, db, explore, runs


class AgentModelsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg = config.load(None)
            cls.conn = db.connect(cls.cfg.dsn)
            db.require_migrated(cls.conn, cls.cfg.migrations_dir)
        except ConfigError as e:
            raise unittest.SkipTest(f"DB 연결/마이그레이션 불가: {e}") from None
        if "baseline-demo" not in {t["target_id"] for t in json.loads(
                (cls.cfg.home / "config" / "scope.json").read_text(encoding="utf-8"))["targets"]}:
            raise unittest.SkipTest("scope.json 에 baseline-demo 대상이 없음")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def _create(self, agent_models, use_llm=True):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-agent-models",
                              use_llm=use_llm, agent_models=agent_models)
        self.addCleanup(self.conn.commit)
        return run

    def test_stored_on_run(self):
        # 설치 여부 검증은 Ollama 연결 불가(None)면 건너뛰므로 고정 모델명으로 저장 경로만 확인한다
        with mock.patch.object(explore, "installed_ollama_models", return_value=["m1:latest"]):
            run = self._create({"worker": "ollama:m1"})
        self.assertEqual(run["llm_agent_models"], {"worker": "ollama:m1"})

    def test_rejects_unknown_role_profile_missing_model_and_no_llm(self):
        with mock.patch.object(explore, "installed_ollama_models", return_value=["m1:latest"]):
            with self.assertRaises(ConfigError):
                self._create({"nope": "ollama:m1"})
            with self.assertRaises(ConfigError):
                self._create({"worker": "no-such-profile"})
            with self.assertRaises(ConfigError):
                self._create({"worker": "ollama:not-installed"})
        with self.assertRaises(ConfigError):
            self._create({"worker": "ollama:m1"}, use_llm=False)

    def test_llm_for_default_override_and_cache(self):
        default = object()
        ctx = SimpleNamespace(llm=default, cfg=self.cfg, conn=self.conn,
                              run={"id": None, "llm_provider": "ollama", "llm_agent_models": {"reporter": "ollama:small"}})
        self.assertIs(explore.llm_for(ctx, "worker"), default)  # 지정 없음 -> 실행 기본 모델
        with mock.patch.object(OllamaClient, "check_ready", return_value={}):
            a = explore.llm_for(ctx, "reporter")
            b = explore.llm_for(ctx, "reporter")
        self.assertIsNot(a, default)
        self.assertEqual(a.model, "small")
        self.assertIs(a, b)  # 같은 지정은 클라이언트 공유

    def test_no_llm_means_none_even_with_override(self):
        ctx = SimpleNamespace(llm=None, run={"llm_agent_models": {"worker": "ollama:small"}})
        self.assertIsNone(explore.llm_for(ctx, "worker"))


if __name__ == "__main__":
    unittest.main()
