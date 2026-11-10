"""Triggers 웹 API(GET/POST/PUT/delete /api/runs/{run}/triggers...)를 확인한다.

- 생성은 confirm 필수, 알 수 없는 condition_kind·존재하지 않는 도구·이 실행 소속이 아닌 asset_id 를 거부.
- 켜기/끄기, 삭제가 실제로 DB 를 바꾼다.
- 다른 실행의 트리거는 안 보임/안 건드림(run_id 로 범위가 묶임).
"""
import json
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db, runs


class TriggersApiTest(unittest.TestCase):
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
        if "baseline-demo" not in {t["target_id"] for t in json.loads(
                (real.home / "config" / "scope.json").read_text(encoding="utf-8"))["targets"]}:
            raise unittest.SkipTest("scope.json 에 baseline-demo 대상이 없음")
        cls.deps = deps
        app.dependency_overrides[deps.current_user] = lambda: "pytest-triggers"
        cls.app, cls.client = app, TestClient(app)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "app"):
            cls.app.dependency_overrides.clear()

    def setUp(self):
        self.conn = db.connect(self.cfg.dsn)
        self.addCleanup(self.conn.close)

    def _run(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-triggers", use_llm=True)
        self.addCleanup(self.conn.commit)
        self.conn.commit()  # 웹 API 는 다른 커넥션을 쓰므로 커밋해야 보임
        return run

    def _asset_id(self, run):
        return str(self.conn.execute("SELECT asset_id FROM run_assets WHERE run_id=%s LIMIT 1",
                                     (run["id"],)).fetchone()["asset_id"])

    def _body(self, run, **overrides):
        body = {"name": "t1", "condition_kind": "interval", "condition_args": {"seconds": 300},
                "tool": "net.dns_resolve", "args": {"host": "localhost"}, "confirm": True,
                "target_asset_id": self._asset_id(run)}
        body.update(overrides)
        return body

    def test_create_requires_confirm(self):
        run = self._run()
        r = self.client.post(f"/api/runs/{run['run_code']}/triggers", json=self._body(run, confirm=False))
        self.assertEqual(r.status_code, 400)

    def test_create_rejects_unknown_condition_kind(self):
        run = self._run()
        r = self.client.post(f"/api/runs/{run['run_code']}/triggers", json=self._body(run, condition_kind="on_sunrise"))
        self.assertEqual(r.status_code, 422)

    def test_create_rejects_unknown_tool(self):
        run = self._run()
        r = self.client.post(f"/api/runs/{run['run_code']}/triggers", json=self._body(run, tool="no.such.tool"))
        self.assertEqual(r.status_code, 422)

    def test_create_requires_target_asset_id(self):
        run = self._run()
        body = self._body(run)
        del body["target_asset_id"]
        r = self.client.post(f"/api/runs/{run['run_code']}/triggers", json=body)
        self.assertEqual(r.status_code, 422)

    def test_create_rejects_asset_not_in_this_run(self):
        run = self._run()
        fake_asset_id = "00000000-0000-0000-0000-000000000000"  # 이 환경엔 대상이 하나뿐이라 가짜 id 로 확인
        r = self.client.post(f"/api/runs/{run['run_code']}/triggers",
                             json=self._body(run, target_asset_id=fake_asset_id))
        self.assertEqual(r.status_code, 422)

    def test_create_list_toggle_delete_roundtrip(self):
        run = self._run()
        r = self.client.post(f"/api/runs/{run['run_code']}/triggers", json=self._body(run))
        self.assertEqual(r.status_code, 200)
        trigger_id = r.json()["id"]

        listed = self.client.get(f"/api/runs/{run['run_code']}/triggers").json()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], trigger_id)
        self.assertTrue(listed[0]["enabled"])

        toggled = self.client.put(f"/api/runs/{run['run_code']}/triggers/{trigger_id}",
                                  json={"enabled": False, "confirm": True})
        self.assertEqual(toggled.status_code, 200)
        self.assertFalse(toggled.json()["enabled"])

        deleted = self.client.post(f"/api/runs/{run['run_code']}/triggers/{trigger_id}/delete",
                                   json={"confirm": True})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get(f"/api/runs/{run['run_code']}/triggers").json(), [])

    def test_triggers_are_scoped_to_their_own_run(self):
        run_a, run_b = self._run(), self._run()
        self.client.post(f"/api/runs/{run_a['run_code']}/triggers", json=self._body(run_a))
        self.assertEqual(self.client.get(f"/api/runs/{run_b['run_code']}/triggers").json(), [])
        self.assertEqual(len(self.client.get(f"/api/runs/{run_a['run_code']}/triggers").json()), 1)


if __name__ == "__main__":
    unittest.main()
