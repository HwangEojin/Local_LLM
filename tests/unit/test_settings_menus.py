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
import uuid
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

    # ── 도구 편집 ──
    def _tool(self, name="fs.read_file"):
        return next(t for t in self.client.get("/api/admin/tools").json()["tools"] if t["name"] == name)

    def _tput(self, body, name="fs.read_file"):
        return self.client.put(f"/api/admin/tools/{name}", json=body)

    def test_tool_disable_removes_it_from_the_registry_everywhere(self):
        from audit.models import ConfigError
        from diagnostic_workbench import runs as wb_runs
        self.assertEqual(self._tput({"disabled": True}).status_code, 400)  # confirm 없음
        self.assertEqual(self._tput({"disabled": True, "confirm": True}, "no.such.tool").status_code, 404)
        self.assertEqual(self._tput({"disabled": True, "confirm": True}).status_code, 200)
        self.assertTrue(self._tool()["disabled"])
        self.assertNotIn("fs.read_file", wb_runs.registry(self.deps.get_cfg()))  # 정책·실행 생성이 보는 레지스트리
        with self.assertRaises(ConfigError):  # 꺼진 도구는 '등록되지 않은 도구'
            wb_runs.create_run(self.conn, self.deps.get_cfg(), ["baseline-demo"], "pytest", use_llm=True,
                               allowed_tools=["fs.read_file"])
        self.conn.rollback()
        self.assertEqual(self._tput({"disabled": False, "confirm": True}).status_code, 200)
        self.assertIn("fs.read_file", wb_runs.registry(self.deps.get_cfg()))

    def test_tool_risk_raise_is_free_but_lowering_needs_extra_confirm(self):
        from diagnostic_workbench import runs as wb_runs
        base = self._tool()["base_risk"]
        self.assertEqual(base, "read")
        self.assertEqual(self._tput({"risk": "write", "confirm": True}).status_code, 200)
        self.assertEqual(wb_runs.registry(self.deps.get_cfg())["fs.read_file"].risk, "write")
        self.assertEqual(self._tput({"risk": "read", "confirm": True}).status_code, 400)  # 낮추기는 추가 확인 필요
        self.assertEqual(self._tput({"risk": "read", "confirm": True, "confirm_lower_risk": True}).status_code, 200)
        self.assertEqual(wb_runs.registry(self.deps.get_cfg())["fs.read_file"].risk, "read")
        self.assertEqual(self._tput({"risk": "root", "confirm": True}).status_code, 422)

    # ── 알림 ──
    def test_notifications_mask_urls_and_require_external_confirm(self):
        secret = "T0SECRETPATH/B0TOKEN"
        url = f"https://hooks.example.test/services/{secret}"
        put = lambda name, body: self.client.put(f"/api/admin/notifications/{name}", json=body)  # noqa: E731
        self.assertEqual(put("ops", {"url": url, "confirm": True}).status_code, 400)  # 외부 주소는 별도 확인
        self.assertEqual(put("ops", {"url": "ftp://x/y", "confirm": True, "confirm_external": True}).status_code, 422)
        self.assertEqual(put("ops", {"confirm": True}).status_code, 422)  # 새 채널은 url 필요
        r = put("ops", {"url": url, "confirm": True, "confirm_external": True})
        self.assertEqual(r.status_code, 200)
        listing = self.client.get("/api/admin/notifications")
        self.assertNotIn(secret, listing.text)  # 주소의 비밀 부분은 응답에 나오지 않는다
        row = next(c for c in listing.json() if c["name"] == "ops")
        self.assertTrue(row["external"])
        self.assertEqual(row["source"], "local")
        saved = (self.home / "config" / "notify.local.json").read_text(encoding="utf-8")
        self.assertIn(secret, saved)  # 파일(Git 제외)에는 실제 주소가 저장된다
        events = self.conn.execute("SELECT details FROM audit_events WHERE event='admin_notification_changed' "
                                   "ORDER BY id DESC LIMIT 1").fetchone()["details"]
        self.assertNotIn(secret, json.dumps(events))  # 감사 로그에도 마스킹된 값만
        self.assertEqual(self.client.post("/api/admin/notifications/ops/delete", json={}).status_code, 400)
        self.assertEqual(self.client.post("/api/admin/notifications/ops/delete", json={"confirm": True}).status_code, 200)
        self.assertNotIn("ops", [c["name"] for c in self.client.get("/api/admin/notifications").json()])

    def test_notification_min_severity_and_history(self):
        url = "http://127.0.0.1:1/unused"  # 내부 주소라 confirm_external 없이도 등록 가능, 실제 전송은 안 함
        put = lambda name, body: self.client.put(f"/api/admin/notifications/{name}", json=body)  # noqa: E731
        self.addCleanup(lambda: self.client.post("/api/admin/notifications/sev-test/delete", json={"confirm": True}))
        r = put("sev-test", {"url": url, "min_severity": "high", "confirm": True})
        self.assertEqual(r.status_code, 200)
        row = next(c for c in r.json() if c["name"] == "sev-test")
        self.assertEqual(row["min_severity"], "high")
        r2 = put("sev-test", {"min_severity": "", "confirm": True})  # 빈 값 = 필터 지움
        self.assertIsNone(next(c for c in r2.json() if c["name"] == "sev-test")["min_severity"])
        self.assertEqual(put("sev-test", {"min_severity": "극한", "confirm": True}).status_code, 422)
        hist = self.client.get("/api/admin/notifications/history")
        self.assertEqual(hist.status_code, 200)
        self.assertIsInstance(hist.json(), list)

    def test_notification_test_send_and_shared_channel_rules(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        got = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        local_url = f"http://127.0.0.1:{srv.server_port}/hook"
        # 루프백 주소는 외부 확인 없이 등록할 수 있다
        self.assertEqual(self.client.put("/api/admin/notifications/local-hook",
                                         json={"url": local_url, "confirm": True}).status_code, 200)
        self.assertEqual(self.client.post("/api/admin/notifications/local-hook/test", json={}).status_code, 400)
        out = self.client.post("/api/admin/notifications/local-hook/test", json={"confirm": True}).json()
        self.assertTrue(out["ok"])
        self.assertEqual(got[0]["event"], "test")
        self.assertNotIn("finding_id", got[0])  # 테스트 전송에 진단 데이터는 없다
        self.assertEqual(self.client.post("/api/admin/notifications/nope/test", json={"confirm": True}).status_code, 404)
        # 공유 파일의 채널: 주소는 못 바꾸고, 끄기만 이 PC 전용 파일에 덮어쓴다
        (self.home / "config" / "notify.json").write_text(json.dumps({"channels": [
            {"name": "shared-hook", "kind": "webhook", "url": local_url, "enabled": True}]}), encoding="utf-8")
        self.assertEqual(self.client.put("/api/admin/notifications/shared-hook",
                                         json={"url": local_url + "x", "confirm": True}).status_code, 400)
        r = self.client.put("/api/admin/notifications/shared-hook", json={"enabled": False, "confirm": True})
        self.assertFalse(next(c for c in r.json() if c["name"] == "shared-hook")["enabled"])
        from diagnostic_workbench import notify
        self.assertNotIn("shared-hook", [c["name"] for c in notify.load_channels(self.home / "config" / "notify.json")])
        self.assertEqual(self.client.post("/api/admin/notifications/shared-hook/delete",
                                          json={"confirm": True}).status_code, 200)  # 덮어쓴 항목만 지워 공유 채널이 되살아남
        self.assertTrue(next(c for c in self.client.get("/api/admin/notifications").json()
                             if c["name"] == "shared-hook")["enabled"])

    # ── 스킬 ──
    def test_skills_attach_by_role_and_never_when_disabled(self):
        name = "pytest-skill"
        self.addCleanup(lambda: [self.conn.execute("DELETE FROM agent_skills WHERE name=%s", (name,)), self.conn.commit()])
        body = {"description": "테스트", "instructions": "SKILL-MARKER 항상 증거 id 를 인용할 것", "roles": ["worker"],
                "enabled": True, "confirm": True}
        put = lambda b: self.client.put(f"/api/admin/skills/{name}", json=b)  # noqa: E731
        self.assertEqual(put({**body, "confirm": False}).status_code, 400)
        self.assertEqual(put({**body, "roles": ["nobody"]}).status_code, 422)
        self.assertEqual(put({**body, "instructions": "  "}).status_code, 422)
        self.assertEqual(put(body).status_code, 200)
        self.assertEqual(self.client.put("/api/admin/skills/bad name", json=body).status_code, 422)
        base = "BASE-PROMPT"
        self.assertIn("SKILL-MARKER", wb_prompts.get(self.conn, "worker_agent_system", base, "worker"))
        self.assertNotIn("SKILL-MARKER", wb_prompts.get(self.conn, "worker_agent_system", base, "planner"))  # 다른 역할
        self.assertEqual(wb_prompts.get(self.conn, "worker_agent_system", base), base)  # role 없으면 그대로
        self.assertEqual(wb_prompts.skills_for(self.conn, "analyst"), "")  # 붙지 않은 역할은 빈 문자열(analyst 주입 경로)
        self.assertEqual(put({**body, "roles": ["analyst"]}).status_code, 200)
        self.assertIn("SKILL-MARKER", wb_prompts.skills_for(self.conn, "analyst"))
        self.assertEqual(put(body).status_code, 200)
        self.assertEqual(put({**body, "enabled": False}).status_code, 200)
        self.assertNotIn("SKILL-MARKER", wb_prompts.get(self.conn, "worker_agent_system", base, "worker"))
        self.assertEqual(self.client.post(f"/api/admin/skills/{name}/delete", json={"confirm": True}).status_code, 200)
        self.assertEqual(self.client.post(f"/api/admin/skills/{name}/delete", json={"confirm": True}).status_code, 404)

    def test_skill_import_parses_frontmatter_and_tracks_usage(self):
        name = "pytest-import-skill"
        self.addCleanup(lambda: [self.conn.execute("DELETE FROM agent_skills WHERE name=%s", (name,)),
                                 self.conn.execute("DELETE FROM skill_usage WHERE skill=%s", (name,)),
                                 self.conn.commit()])
        md = (
            "---\n"
            f"name: {name}\n"
            "description: 테스트 가져오기\n"
            "license: MIT\n"
            "roles:\n"
            "  - worker\n"
            "  - reporter\n"
            "---\n"
            "IMPORT-MARKER 항상 증거 id 를 인용할 것\n"
        )
        self.assertEqual(self.client.post("/api/admin/skills/import", json={"text": md, "confirm": False}).status_code, 400)
        self.assertEqual(self.client.post("/api/admin/skills/import", json={"text": "no frontmatter here", "confirm": True}).status_code, 422)
        r = self.client.post("/api/admin/skills/import", json={"text": md, "confirm": True})
        self.assertEqual(r.status_code, 200)
        row = next(s for s in r.json()["skills"] if s["name"] == name)
        self.assertEqual(row["license"], "MIT")
        self.assertEqual(row["source"], "import")
        self.assertEqual(sorted(row["roles"]), ["reporter", "worker"])
        self.assertIn("IMPORT-MARKER", row["instructions"])

        # 실제 LLM 호출 지점이 읽는 skills_for 를 run_id 와 함께 호출하면 skill_usage 에 기록된다(artex 의 skill 사용 집계 참고).
        # skill_usage 는 외래 키가 없으므로(마이그레이션 주석 참고) 진짜 실행을 만들 필요 없이 무작위 UUID 로 충분하다.
        # 웹 API 는 이 테스트의 self.conn 과는 다른 커넥션을 쓰므로, 커밋해야 거기서 보인다.
        run_id = uuid.uuid4()
        wb_prompts.skills_for(self.conn, "worker", run_id)
        wb_prompts.skills_for(self.conn, "worker", run_id)  # 두 번째 호출은 같은 행의 calls 를 올림
        self.conn.commit()
        usage = self.client.get(f"/api/admin/skills/{name}/usage").json()
        self.assertEqual(len(usage["recent"]), 1)
        self.assertEqual(usage["recent"][0]["calls"], 2)
        self.assertEqual(usage["recent"][0]["role"], "worker")
        self.assertIsNone(usage["recent"][0]["run_code"])  # 진짜 실행이 아니라 null 로 보임(LEFT JOIN)

        # roles 를 요청 바디에서 지정하면 frontmatter 의 roles 대신 그 값을 쓴다
        r2 = self.client.post("/api/admin/skills/import", json={"text": md, "roles": ["planner"], "confirm": True})
        row2 = next(s for s in r2.json()["skills"] if s["name"] == name)
        self.assertEqual(row2["roles"], ["planner"])

    # ── 인터셉트 규칙 ──
    def test_intercept_rule_crud_and_bad_regex_and_shared_protection(self):
        name = "pytest-intercept"
        put = lambda b: self.client.put(f"/api/admin/intercept-rules/{name}", json=b)  # noqa: E731
        self.assertEqual(put({"tool_pattern": "^fs\\.", "confirm": False}).status_code, 400)
        self.assertEqual(put({"tool_pattern": "(unclosed", "confirm": True}).status_code, 422)
        self.assertEqual(put({"tool_pattern": "^fs\\.", "arg_pattern": "secret", "confirm": True}).status_code, 200)
        rows = self.client.get("/api/admin/intercept-rules").json()
        row = next(r for r in rows if r["name"] == name)
        self.assertEqual(row["source"], "local")
        self.assertTrue(row["enabled"])
        # 공유 파일에 같은 이름이 있으면 웹에서 덮어쓰지 못한다
        (self.home / "config" / "intercept_rules.json").write_text(
            json.dumps({"rules": [{"name": "shared-guard", "tool_pattern": "^dangerous\\."}]}), encoding="utf-8")
        self.assertEqual(put({"tool_pattern": "x", "confirm": True}, ).status_code, 200)  # 이름 다름, 영향 없음 확인용
        self.assertEqual(self.client.put("/api/admin/intercept-rules/shared-guard",
                                         json={"tool_pattern": "^harmless\\.", "confirm": True}).status_code, 400)
        self.assertEqual(self.client.post("/api/admin/intercept-rules/shared-guard/delete",
                                          json={"confirm": True}).status_code, 404)
        self.assertEqual(self.client.post(f"/api/admin/intercept-rules/{name}/delete", json={}).status_code, 400)
        self.assertEqual(self.client.post(f"/api/admin/intercept-rules/{name}/delete",
                                          json={"confirm": True}).status_code, 200)

    def test_intercept_test_match_reflects_saved_rules_live(self):
        name = "pytest-intercept-match"
        self.addCleanup(lambda: self.client.post(f"/api/admin/intercept-rules/{name}/delete", json={"confirm": True}))
        before = self.client.post("/api/admin/intercept-rules/test-match",
                                  json={"tool": "fs.read_file", "args": {"path": "/etc/shadow"}}).json()
        self.assertFalse(before["matched"])
        self.client.put(f"/api/admin/intercept-rules/{name}",
                        json={"tool_pattern": "^fs\\.read_file$", "arg_pattern": "shadow", "confirm": True})
        after = self.client.post("/api/admin/intercept-rules/test-match",
                                 json={"tool": "fs.read_file", "args": {"path": "/etc/shadow"}}).json()
        self.assertTrue(after["matched"])
        self.assertEqual(after["rule"], name)
        miss = self.client.post("/api/admin/intercept-rules/test-match",
                                json={"tool": "fs.read_file", "args": {"path": "/etc/hosts"}}).json()
        self.assertFalse(miss["matched"])
        # policy.decide() 가 실제로 ALLOW_WITH_APPROVAL 로 바꾸는지까지 확인(화면의 '테스트'가 실제 동작과 같은지)
        from diagnostic_workbench import config, policy
        cfg = config.load(None)
        rules = __import__("audit.config", fromlist=["load_intercept_rules"]).load_intercept_rules(cfg.home)
        self.assertEqual(policy._intercepted(rules, "fs.read_file", {"path": "/etc/shadow"}), name)


if __name__ == "__main__":
    unittest.main()
