"""웹 관리 API(src/webapi/routers/admin.py)와 진단 ID(run_code)를 확인한다.

- 설정 파일은 임시 홈(AUDIT_HOME)에 복사해서 쓰므로 저장소의 config/ 는 바뀌지 않는다.
- 확인(confirm) 없는 변경 거부, scope 검증(만료·중복), 웹에서 바꿀 수 없는 항목 거부, 중지 스위치 켜기/해제,
  MCP 활성 토글, 변경 전/후가 담긴 감사 이벤트, 진단 ID 형식과 조회를 본다.
- DB 는 다른 테스트와 같이 실제 PostgreSQL 을 쓴다. audit_events 는 추가 전용이라 actor 가 web:pytest-admin 인 행이 남는다.
"""
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from audit.models import ConfigError
from diagnostic_workbench import config, db, runs

ROOT = Path(__file__).resolve().parents[2]


class AdminApiTest(unittest.TestCase):
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
        (self.home / "data" / "STOP").unlink(missing_ok=True)

    def _scope(self):
        return json.loads((self.home / "config" / "scope.json").read_text(encoding="utf-8"))

    def _last_audit(self, event):
        cfg = self.deps.get_cfg()
        conn = db.connect(cfg.dsn)
        try:
            return conn.execute("SELECT details FROM audit_events WHERE event=%s ORDER BY id DESC LIMIT 1",
                                (event,)).fetchone()["details"]
        finally:
            conn.close()

    # ---- scope
    def test_scope_requires_confirm(self):
        r = self.client.put("/api/admin/scope", json={"scope": self._scope()})
        self.assertEqual(r.status_code, 400)

    def test_scope_rejects_expired_and_duplicate_targets(self):
        s = self._scope()
        r = self.client.put("/api/admin/scope", json={"scope": {**s, "valid_until": "2000-01-01"}, "confirm": True})
        self.assertEqual(r.status_code, 422)
        dup = {**s, "targets": s["targets"] * 2}
        r = self.client.put("/api/admin/scope", json={"scope": dup, "confirm": True})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self._scope(), s)  # 거부된 변경은 파일을 바꾸지 않는다

    def test_scope_dangerous_needs_extra_confirm_and_change_is_audited(self):
        s = self._scope()
        risky = {**s, "allowed_risks": ["read", "dangerous"]}
        r = self.client.put("/api/admin/scope", json={"scope": risky, "confirm": True})
        self.assertEqual(r.status_code, 400)
        ok = {**s, "targets": [{**s["targets"][0], "code": "TST"}]}
        r = self.client.put("/api/admin/scope", json={"scope": ok, "confirm": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._scope()["targets"][0]["code"], "TST")
        det = self._last_audit("admin_scope_changed")
        self.assertEqual(det["actor"], "web:pytest-admin")
        self.assertNotIn("code", det["before"]["targets"][0])
        self.assertEqual(det["after"]["targets"][0]["code"], "TST")
        self.assertTrue(list((self.home / "data" / "config_backups").glob("scope.json.*.bak")))

    # ---- 제외/예산, 설정
    def test_workbench_writes_exclude_and_keeps_readonly_paths(self):
        r = self.client.put("/api/admin/workbench", json={"exclude": [" admin.example.test ", ""], "confirm": True,
                                                          "budget": {"max_tasks": 10}})
        self.assertEqual(r.status_code, 200, r.text)
        saved = json.loads((self.home / "config" / "workbench.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["exclude"], ["admin.example.test"])
        self.assertEqual(self.client.get("/api/admin/workbench").json()["budget"]["max_tasks"], 10)
        r = self.client.put("/api/admin/workbench", json={"budget": {"max_tasks": 0}, "confirm": True})
        self.assertEqual(r.status_code, 422)

    def test_settings_blocks_external_llm_url_and_readonly_keys(self):
        r = self.client.put("/api/admin/settings", json={"ollama": {"base_url": "http://evil.example.com:11434"},
                                                         "confirm": True})
        self.assertEqual(r.status_code, 422)
        r = self.client.put("/api/admin/settings", json={"ollama": {"nope": 1}, "confirm": True})
        self.assertEqual(r.status_code, 422)
        r = self.client.put("/api/admin/settings", json={"ollama": {"timeout_s": 120}, "confirm": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.get("/api/admin/settings").json()["ollama"]["timeout_s"], 120)

    # ---- 중지 스위치
    def test_kill_switch_engage_release(self):
        self.assertFalse(self.client.get("/api/admin/kill-switch").json()["active"])
        self.assertEqual(self.client.post("/api/admin/kill-switch/engage", json={"reason": " "}).status_code, 422)
        r = self.client.post("/api/admin/kill-switch/engage", json={"reason": "테스트 사고"})
        self.assertTrue(r.json()["active"])
        self.assertTrue((self.home / "data" / "STOP").exists())
        self.assertEqual(self.client.post("/api/admin/kill-switch/release", json={"reason": "x"}).status_code, 400)
        r = self.client.post("/api/admin/kill-switch/release", json={"reason": "원인 제거", "confirm": True})
        self.assertFalse(r.json()["active"])
        self.assertFalse((self.home / "data" / "STOP").exists())
        self.assertEqual(self._last_audit("admin_kill_switch_released")["reason"], "원인 제거")

    # ---- MCP
    def test_mcp_toggle_only_changes_enabled(self):
        name = next(m["name"] for m in self.client.get("/api/admin/mcp").json() if not m["enabled"])
        r = self.client.put(f"/api/admin/mcp/{name}", json={"enabled": True})
        self.assertEqual(r.status_code, 400)
        r = self.client.put(f"/api/admin/mcp/{name}", json={"enabled": True, "confirm": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(next(m for m in r.json() if m["name"] == name)["enabled"])
        shared = json.loads((self.home / "config" / "mcp_servers.json").read_text(encoding="utf-8"))
        local = json.loads((self.home / "config" / "mcp_servers.local.json").read_text(encoding="utf-8"))
        self.assertEqual({**local["servers"][name], "enabled": shared["servers"][name]["enabled"]},
                         shared["servers"][name])
        self.assertEqual(self.client.put("/api/admin/mcp/nope", json={"enabled": True, "confirm": True}).status_code, 404)

    # ---- 진단 ID
    def test_run_code_format_and_lookup(self):
        cfg = self.deps.get_cfg()
        conn = db.connect(cfg.dsn)
        self.addCleanup(conn.close)
        run = runs.create_run(conn, cfg, ["baseline-demo"], "pytest-run-code", use_llm=True)
        self.assertRegex(run["run_code"], r"^[A-Z0-9]{2,6}-\d{8}-\d{2,}$")
        self.assertEqual(db.resolve_run(conn, run["run_code"].lower())["id"], run["id"])
        self.assertEqual(db.resolve_run(conn, str(run["id"])[:8])["id"], run["id"])  # 기존 UUID 접두어 조회 유지
        run2 = runs.create_run(conn, cfg, ["baseline-demo"], "pytest-run-code", use_llm=True)
        self.assertNotEqual(run["run_code"], run2["run_code"])
        self.assertEqual(self.client.get(f"/api/runs/{run['run_code']}").json()["run"]["id"], str(run["id"]))

    def test_target_abbr_rules(self):
        self.assertEqual(runs.target_abbr("baseline-demo"), "BASE")
        self.assertEqual(runs.target_abbr("web_1"), "WEB")
        self.assertEqual(runs.target_abbr("x", "ABC"), "ABC")
        self.assertEqual(runs.target_abbr("---"), "RUN")
        self.assertTrue(re.fullmatch(r"[A-Z0-9]+", runs.target_abbr("가나다 api")))

    def test_audit_export_csv_and_json(self):
        import csv
        import io
        conn = db.connect(config.load(None).dsn)
        self.addCleanup(conn.close)
        db.audit(conn, None, "pytest_export_marker", {"from": "test_audit_export_csv_and_json"})
        conn.commit()
        r = self.client.get("/api/admin/audit/export", params={"event": "pytest_export_marker", "format": "json"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("application/json", r.headers["content-type"])
        rows = r.json()
        self.assertTrue(rows)
        self.assertTrue(all("pytest_export_marker" in row["event"] for row in rows))
        r2 = self.client.get("/api/admin/audit/export")  # 기본 csv, 필터 없음
        self.assertEqual(r2.status_code, 200)
        self.assertIn("text/csv", r2.headers["content-type"])
        parsed = list(csv.reader(io.StringIO(r2.text.lstrip("﻿"))))  # BOM: 엑셀이 한글 CSV 를 바로 읽게
        self.assertEqual(parsed[0], ["id", "ts", "event", "run_code", "details"])
        self.assertEqual(self.client.get("/api/admin/audit/export", params={"format": "xml"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
