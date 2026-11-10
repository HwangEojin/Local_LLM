"""역할별 '이름 있는 프로파일'(F3a 절충안, UPGRADE_PLAN.md)을 확인한다.

- prompts.get() 은 profile_name 을 주면 그 프로파일의 prompt_overrides 를 전역 agent_prompts 보다 먼저 본다.
  프로파일에 그 이름의 오버라이드가 없으면 전역 값, 그것도 없으면 default 로 그대로 떨어진다(기존 동작 보존).
- runs.create_run 은 agent_profile_names 를 검증하고(알 수 없는 역할/존재하지 않는 프로파일 거부),
  프로파일의 llm_profile 을 역할의 명시적 agent_models 지정이 없을 때만 기본값으로 채운다.
"""
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db, prompts, runs


class AgentProfilesDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg = config.load(None)
            cls.conn = db.connect(cls.cfg.dsn)
            db.require_migrated(cls.conn, cls.cfg.migrations_dir)
        except ConfigError as e:
            raise unittest.SkipTest(f"DB 연결/마이그레이션 불가: {e}") from None

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def _profile(self, role, name, prompt_overrides=None, llm_profile=None):
        row = prompts.create_profile(self.conn, role, name, prompt_overrides or {}, llm_profile, operator="pytest")
        self.addCleanup(lambda: prompts.delete_profile(self.conn, row["id"]))
        self.addCleanup(self.conn.commit)
        self.conn.commit()
        return row

    def test_get_prefers_profile_override_over_global_default(self):
        self._profile("worker", "aggressive", {"worker_agent_system": "aggressive worker override text here"})
        text = prompts.get(self.conn, "worker_agent_system", "DEFAULT", "worker", None, "aggressive")
        self.assertEqual(text, "aggressive worker override text here")

    def test_get_falls_back_to_default_when_profile_has_no_override_for_this_prompt(self):
        self._profile("planner", "cautious", {})  # explore_system 오버라이드 없음
        text = prompts.get(self.conn, "explore_system", "DEFAULT", "planner", None, "cautious")
        self.assertEqual(text, "DEFAULT")

    def test_get_without_profile_name_is_unaffected(self):
        self._profile("worker", "aggressive2", {"worker_agent_system": "should not leak"})
        text = prompts.get(self.conn, "worker_agent_system", "DEFAULT", "worker", None, None)
        self.assertEqual(text, "DEFAULT")

    def test_create_run_rejects_unknown_role_in_agent_profile_names(self):
        with self.assertRaises(ConfigError):
            runs._check_agent_profiles(self.conn, {"no-such-role": "x"})

    def test_create_run_rejects_unknown_profile_name(self):
        with self.assertRaises(ConfigError):
            runs._check_agent_profiles(self.conn, {"worker": "no-such-profile"})

    def _require_baseline_demo(self):
        import json
        if "baseline-demo" not in {t["target_id"] for t in json.loads(
                (self.cfg.home / "config" / "scope.json").read_text(encoding="utf-8"))["targets"]}:
            raise unittest.SkipTest("scope.json 에 baseline-demo 대상이 없음")

    def test_create_run_fills_model_gap_from_profile_llm_profile(self):
        # "ollama:" 접두어면 llm_profiles.json 에 등록돼 있을 필요가 없다(설치 여부만 느슨하게 확인) —
        # 이 환경에 llm_profiles.json 이 비어 있을 수 있어 이 형식으로 검증을 통과시킨다.
        self._profile("worker", "with-model", {}, llm_profile="ollama:qwen2.5:14b")
        self._require_baseline_demo()
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-profile", checklist_id=None,
                              use_llm=True, agent_profile_names={"worker": "with-model"})
        self.addCleanup(self.conn.commit)
        self.assertEqual(run["llm_agent_models"].get("worker"), "ollama:qwen2.5:14b")
        self.assertEqual(run["agent_profile_names"].get("worker"), "with-model")

    def test_explicit_agent_model_wins_over_profile_llm_profile(self):
        self._profile("worker", "with-model2", {}, llm_profile="ollama:qwen2.5:14b")
        self._require_baseline_demo()
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-profile", checklist_id=None,
                              use_llm=True, agent_models={"worker": "ollama:qwen2.5:7b-instruct"},
                              agent_profile_names={"worker": "with-model2"})
        self.addCleanup(self.conn.commit)
        self.assertEqual(run["llm_agent_models"].get("worker"), "ollama:qwen2.5:7b-instruct")


if __name__ == "__main__":
    unittest.main()
