"""커스텀 에이전트 웹 API(ARTEX 스타일 자유 에이전트 생성, 2026-11-13)를 확인한다.

- 생성/수정은 confirm 필수, 이름 중복·빈 allowed_tools·등록 안 된 도구를 거부.
- 삭제는 참조 중인 run_triggers 가 있으면 409로 막는다(먼저 트리거를 지우거나 바꾸라고 안내).
"""
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db, runs


class CustomAgentsApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            real = config.load(None)
            conn = db.connect(real.dsn)
            db.require_migrated(conn, real.migrations_dir)
            conn.close()
            from fastapi.testclient import TestClient
            from webapi import deps
            from webapi.main import app
        except (ConfigError, ImportError) as e:
            raise unittest.SkipTest(f"DB/웹 의존성 불가: {e}") from None
        cls.cfg = real
        cls.deps = deps
        app.dependency_overrides[deps.current_user] = lambda: "pytest-custom-agents"
        cls.app, cls.client = app, TestClient(app)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "app"):
            cls.app.dependency_overrides.clear()

    def setUp(self):
        self.conn = db.connect(self.cfg.dsn)
        self.addCleanup(self.conn.close)

    def _body(self, **overrides):
        body = {"name": f"pytest-agent-{id(overrides) or 1}",
                "system_prompt": "테스트용 시스템 프롬프트 문구입니다 — 충분히 길게 적어 둠",
                "allowed_tools": ["net.dns_resolve"], "confirm": True}
        body.update(overrides)
        return body

    def test_create_requires_confirm(self):
        r = self.client.post("/api/admin/custom-agents", json=self._body(confirm=False))
        self.assertEqual(r.status_code, 400)

    def test_create_rejects_empty_allowed_tools(self):
        r = self.client.post("/api/admin/custom-agents", json=self._body(allowed_tools=[]))
        self.assertEqual(r.status_code, 422)

    def test_create_rejects_unknown_tool(self):
        r = self.client.post("/api/admin/custom-agents", json=self._body(allowed_tools=["no.such.tool"]))
        self.assertEqual(r.status_code, 422)

    def test_create_rejects_short_prompt(self):
        r = self.client.post("/api/admin/custom-agents", json=self._body(system_prompt="짧음"))
        self.assertEqual(r.status_code, 422)

    def test_create_list_update_delete_round_trip(self):
        name = "pytest-roundtrip-agent"
        self.addCleanup(lambda: (self.conn.execute("DELETE FROM custom_agents WHERE name=%s", (name,)),
                                 self.conn.commit()))

        created = self.client.post("/api/admin/custom-agents", json=self._body(name=name))
        self.assertEqual(created.status_code, 200, created.text)
        agent_id = created.json()["id"]

        listed = self.client.get("/api/admin/custom-agents").json()
        self.assertIn(name, [a["name"] for a in listed])

        updated = self.client.put(f"/api/admin/custom-agents/{agent_id}",
                                  json=self._body(name=name, allowed_tools=["net.dns_resolve", "net.http_head"]))
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(sorted(updated.json()["allowed_tools"]), ["net.dns_resolve", "net.http_head"])

        deleted = self.client.post(f"/api/admin/custom-agents/{agent_id}/delete", json={"confirm": True})
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertNotIn(name, [a["name"] for a in self.client.get("/api/admin/custom-agents").json()])

    def test_duplicate_name_rejected(self):
        name = "pytest-dup-agent"
        self.addCleanup(lambda: (self.conn.execute("DELETE FROM custom_agents WHERE name=%s", (name,)),
                                 self.conn.commit()))
        self.assertEqual(self.client.post("/api/admin/custom-agents", json=self._body(name=name)).status_code, 200)
        dup = self.client.post("/api/admin/custom-agents", json=self._body(name=name))
        self.assertEqual(dup.status_code, 409)

    def test_delete_blocked_while_referenced_by_a_trigger(self):
        name = "pytest-referenced-agent"

        def cleanup():
            self.conn.execute("DELETE FROM run_triggers WHERE name='ref-test'")
            self.conn.execute("DELETE FROM custom_agents WHERE name=%s", (name,))
            self.conn.commit()

        self.addCleanup(cleanup)
        agent_id = self.client.post("/api/admin/custom-agents", json=self._body(name=name)).json()["id"]

        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-referenced", use_llm=True)
        self.conn.commit()
        asset_id = self.conn.execute("SELECT asset_id FROM run_assets WHERE run_id=%s LIMIT 1",
                                     (run["id"],)).fetchone()["asset_id"]
        self.conn.execute(
            "INSERT INTO run_triggers (run_id, name, condition_kind, custom_agent_id, target_asset_id, created_by) "
            "VALUES (%s,'ref-test','interval',%s,%s,'pytest')", (run["id"], agent_id, asset_id))
        self.conn.commit()

        r = self.client.post(f"/api/admin/custom-agents/{agent_id}/delete", json={"confirm": True})
        self.assertEqual(r.status_code, 409)


if __name__ == "__main__":
    unittest.main()
