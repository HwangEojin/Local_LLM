"""다중 LLM 프로파일 풀을 확인한다.

- audit.config.load_llm_profiles: 파일 없으면 빈 딕셔너리, 이름 중복은 거부.
- runs.create_run: 모르는 llm_profile 은 거부, 아는 프로파일이면 그 kind 로 llm_provider 를 맞추고
  llm_profile_name 을 저장한다.
- explore.make_llm: 프로파일이 settings.json 의 기본 블록 위에 모델/주소 등을 덮어쓴다(실제 Ollama 가
  있으면 실제로 연결해 확인, 없으면 건너뜀 — 이 플러밍 자체는 결정론적이라 skip 보다 real 우선).
"""
import json
import tempfile
import unittest
from pathlib import Path

from audit import config as audit_config
from audit.models import ConfigError
from diagnostic_workbench import config, db, explore, runs


class LoadLlmProfiles(unittest.TestCase):
    def test_missing_file_means_no_profiles(self):
        self.assertEqual(audit_config.load_llm_profiles(Path(tempfile.mkdtemp())), {})

    def test_loads_by_name(self):
        home = Path(tempfile.mkdtemp())
        (home / "config").mkdir()
        (home / "config" / "llm_profiles.json").write_text(json.dumps({"profiles": [
            {"name": "p1", "kind": "ollama", "model": "m1"},
            {"name": "p2", "kind": "claude_cli", "model": "m2"},
        ]}), encoding="utf-8")
        profiles = audit_config.load_llm_profiles(home)
        self.assertEqual(set(profiles), {"p1", "p2"})
        self.assertEqual(profiles["p1"]["model"], "m1")

    def test_rejects_duplicate_name(self):
        home = Path(tempfile.mkdtemp())
        (home / "config").mkdir()
        (home / "config" / "llm_profiles.json").write_text(json.dumps({"profiles": [
            {"name": "p1", "kind": "ollama"}, {"name": "p1", "kind": "claude_cli"},
        ]}), encoding="utf-8")
        with self.assertRaises(ConfigError):
            audit_config.load_llm_profiles(home)


class CreateRunWithProfile(unittest.TestCase):
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
        cls.profiles_path = cls.cfg.home / "config" / "llm_profiles.json"
        cls._had_file = cls.profiles_path.exists()
        cls._backup = cls.profiles_path.read_text(encoding="utf-8") if cls._had_file else None
        cls.profiles_path.write_text(json.dumps({"profiles": [
            # model 은 일부러 안 적는다 — settings.json 의 기존(설치돼 있다고 보장된) 모델로 떨어지게 하고,
            # timeout_s 만 덮어써서 "프로파일이 실제로 반영됐는지"를 check_ready() 실패 없이 확인한다.
            {"name": "test-ollama", "kind": "ollama", "timeout_s": 77},
            {"name": "test-claude", "kind": "claude_cli", "model": "test-claude-model"},
        ]}), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        if cls._had_file:
            cls.profiles_path.write_text(cls._backup, encoding="utf-8")
        else:
            cls.profiles_path.unlink(missing_ok=True)
        if hasattr(cls, "conn"):
            cls.conn.close()

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ConfigError):
            runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-llm-profile",
                            checklist_id=None, use_llm=True, llm_profile="no-such-profile")

    def test_known_profile_sets_provider_and_profile_name(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-llm-profile",
                              checklist_id=None, use_llm=True, llm_profile="test-claude")
        self.addCleanup(self.conn.commit)
        self.assertEqual(run["llm_provider"], "claude_cli")
        self.assertEqual(run["llm_profile_name"], "test-claude")

    def test_make_llm_resolves_profile_overrides(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-llm-profile",
                              checklist_id=None, use_llm=True, llm_profile="test-ollama")
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        try:
            llm = explore.make_llm(ctx)
        except Exception as e:  # 실제 Ollama 를 이 PC 에서 못 쓰면 건너뜀(플러밍 자체는 별개로 확인됨)
            self.skipTest(f"실제 LLM 연결 불가: {e}")
        self.assertEqual(llm.model, self.cfg.settings.ollama["model"])  # 프로파일에 없으면 settings.json 값
        self.assertEqual(llm.cfg["timeout_s"], 77)  # 프로파일이 덮어쓴 값

    def test_unknown_profile_name_on_run_rejected_by_make_llm(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-llm-profile",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        ctx.run = {**ctx.run, "llm_profile_name": "no-such-profile"}
        with self.assertRaises(Exception):
            explore.make_llm(ctx)


if __name__ == "__main__":
    unittest.main()
