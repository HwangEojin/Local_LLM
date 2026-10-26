"""설정 메뉴 세분화(1단계)의 백엔드: 에이전트 프롬프트 편집, 도구 카탈로그, 승인 모음.

- 프롬프트 변경은 확인(confirm) 필수, 알 수 없는 이름·너무 짧은 문구 거부, 기본값과 같으면 오버라이드를 남기지 않는다.
- 편집한 프롬프트는 실제 LLM 호출 지점이 읽는 prompts.get() 에 반영된다(원복까지 확인, 테스트는 항상 원복).
- 설정 파일은 임시 홈에 복사해 쓰므로 저장소의 config/ 는 바뀌지 않는다.
"""
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from audit.models import ConfigError
from diagnostic_workbench import config, db, prompts as wb_prompts

ROOT = Path(__file__).resolve().parents[2]


class SettingsMenusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            real = config.load(None)  # .env 를 읽어 DSN 환경 변수를 채운다
            conn = db.connect(real.dsn)
            db.require_migrated(conn, real.migrations_dir)
            conn.close()
            from fastapi.testclient import TestClient
            from webapi import deps
            from webapi.main import app
        except (ConfigError, ImportError) as e:
            raise unittest.SkipTest(f"DB/웹 의존성 불가: {e}") from None
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = Path(cls.tmp.name)
        shutil.copytree(ROOT / "config", cls.home / "config")
        (cls.home / "data").mkdir()
        cls.old_home = os.environ.get("AUDIT_HOME")
        os.environ["AUDIT_HOME"] = str(cls.home)
        cls.deps = deps
        deps.reset_cfg()
        app.dependency_overrides[deps.current_user] = lambda: "pytest-admin"
        cls.app, cls.client = app, TestClient(app)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "app"):
            cls.app.dependency_overrides.clear()
            if cls.old_home is None:
                os.environ.pop("AUDIT_HOME", None)
            else:
                os.environ["AUDIT_HOME"] = cls.old_home
            cls.deps.reset_cfg()
            cls.tmp.cleanup()

    def setUp(self):
        self.deps.reset_cfg()
        self.conn = db.connect(config.load(None).dsn)
        self.addCleanup(self.conn.close)
        self.addCleanup(lambda: [wb_prompts.reset(self.conn, "worker_agent_system"), self.conn.commit()])

    def _put(self, name, text, confirm=True):
        return self.client.put(f"/api/admin/agents/prompts/{name}", json={"text": text, "confirm": confirm})

    def test_agents_lists_roles_with_defaults(self):
        rows = {r["role"]: r for r in self.client.get("/api/admin/agents").json()}
        self.assertEqual(set(rows), {"planner", "worker", "analyst", "goals", "reporter"})
        self.assertEqual(rows["analyst"]["prompts"], [])  # 체크리스트 분석 프롬프트는 편집 대상 아님
        p = rows["worker"]["prompts"][0]
        self.assertEqual(p["name"], "worker_agent_system")
        self.assertFalse(p["overridden"])
        self.assertEqual(p["text"], p["default"])

    def test_prompt_edit_requires_confirm_valid_name_and_length(self):
        text = "x" * 40
        self.assertEqual(self._put("worker_agent_system", text, confirm=False).status_code, 400)
        self.assertEqual(self._put("no_such_prompt", text).status_code, 404)
        self.assertEqual(self._put("worker_agent_system", "짧음").status_code, 422)

    def test_prompt_edit_reaches_llm_call_site_and_reset_restores(self):
        new = "새 Worker 지침 — " + "a" * 30
        self.assertTrue(self._put("worker_agent_system", new).json()["overridden"])
        self.assertEqual(wb_prompts.get(self.conn, "worker_agent_system", "기본"), new)
        shown = next(p for r in self.client.get("/api/admin/agents").json() if r["role"] == "worker" for p in r["prompts"])
        self.assertTrue(shown["overridden"])
        self.assertEqual(shown["updated_by"], "web:pytest-admin")
        self.assertEqual(self.client.post("/api/admin/agents/prompts/worker_agent_system/reset", json={}).status_code, 400)
        self.client.post("/api/admin/agents/prompts/worker_agent_system/reset", json={"confirm": True})
        self.assertEqual(wb_prompts.get(self.conn, "worker_agent_system", "기본"), "기본")

    def test_prompt_equal_to_default_leaves_no_override(self):
        default = next(p["default"] for c in wb_prompts.catalog() for p in c["prompts"] if p["name"] == "worker_agent_system")
        self.assertFalse(self._put("worker_agent_system", default).json()["overridden"])

    def test_tools_catalog(self):
        out = self.client.get("/api/admin/tools").json()
        self.assertTrue(out["tools"])
        t = out["tools"][0]
        self.assertTrue({"name", "risk", "args_schema", "source", "allowed_by_scope"} <= set(t))
        self.assertTrue(all(x["allowed_by_scope"] == (x["risk"] in out["allowed_risks"]) for x in out["tools"]))

    def test_approvals_overview_shape(self):
        out = self.client.get("/api/admin/approvals").json()
        self.assertIsInstance(out["ttl_h"], int)
        self.assertIsInstance(out["pending"], list)
        self.assertIsInstance(out["recent"], list)

    def test_ttl_save_sequence_keeps_other_workbench_values(self):
        """승인 화면이 쓰는 방식(현재 값을 읽어 전체를 다시 보내 ttl 만 바꿈)이 다른 설정을 초기화하지 않는다."""
        w = self.client.get("/api/admin/workbench").json()
        body = {"exclude": ["keep.example.test"], "lease_s": w["lease_s"] + 1, "max_attempts": w["max_attempts"],
                "approval_ttl_h": w["approval_ttl_h"], "min_call_interval_ms": 7, "budget": w["budget"], "confirm": True}
        self.assertEqual(self.client.put("/api/admin/workbench", json=body).status_code, 200)
        cur = self.client.get("/api/admin/workbench").json()
        body2 = {"exclude": cur["exclude"], "lease_s": cur["lease_s"], "max_attempts": cur["max_attempts"],
                 "approval_ttl_h": 48, "min_call_interval_ms": cur["min_call_interval_ms"], "budget": cur["budget"],
                 "confirm": True}
        self.assertEqual(self.client.put("/api/admin/workbench", json=body2).status_code, 200)
        after = self.client.get("/api/admin/workbench").json()
        self.assertEqual(after["approval_ttl_h"], 48)
        self.assertEqual(after["exclude"], ["keep.example.test"])
        self.assertEqual(after["lease_s"], w["lease_s"] + 1)
        self.assertEqual(after["min_call_interval_ms"], 7)
        self.assertEqual(self.client.get("/api/admin/approvals").json()["ttl_h"], 48)


if __name__ == "__main__":
    unittest.main()
