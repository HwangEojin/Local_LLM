"""웹에서 MCP 서버 추가(발견 → 선택 등록) 흐름을 실제 MCP(SSE) 서버로 확인한다.

- 연결 테스트·도구 발견은 아무것도 저장하지 않고, 연결 실패는 사유와 함께 ok=false 로 돌려준다.
- 등록은 확인(confirm) 필수, 공유 파일 서버 덮어쓰기·서버에 없는 도구·범위 검사 없는 read 는 거부하고, 스키마는 서버가
  알려 준 것을 쓴다. 등록된 도구는 이 PC 전용 파일에 저장되어 레지스트리에 위험도·scope_args 와 함께 올라간다.
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
from diagnostic_workbench import config, db, runs as wb_runs

ROOT = Path(__file__).resolve().parents[2]


class McpRegisterTest(unittest.TestCase):
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

    @classmethod
    def _start_server(cls):
        import socket
        import subprocess
        import sys
        import time
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        cls.port = s.getsockname()[1]
        s.close()
        cls.proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name("_fake_mcp_server.py")), str(cls.port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls.proc.kill)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.1)
        raise unittest.SkipTest("가짜 MCP 서버가 뜨지 않음")

    def setUp(self):
        self.deps.reset_cfg()
        self.conn = db.connect(config.load(None).dsn)
        self.addCleanup(self.conn.close)
        if not hasattr(type(self), "proc"):
            type(self)._start_server()
        self.url = f"http://127.0.0.1:{self.port}/sse"

    def _reg(self, tools, **kw):
        body = {"url": self.url, "tools": tools, "confirm": True, **kw}
        return self.client.post("/api/admin/mcp/fake-test/register", json=body)

    def test_discover_lists_tools_and_suggests_scope_without_saving(self):
        out = self.client.post("/api/admin/mcp/discover", json={"url": self.url}).json()
        self.assertTrue(out["ok"], out)
        tools = {t["name"]: t for t in out["tools"]}
        self.assertEqual(set(tools), {"fetch_page", "read_note"})
        self.assertTrue(tools["fetch_page"]["schema_ok"])
        self.assertEqual(tools["fetch_page"]["suggested_scope"], {"url": "url"})  # 이름으로 짐작한 후보(운영자가 확인)
        self.assertEqual(tools["read_note"]["suggested_scope"], {})
        self.assertFalse((self.home / "config" / "mcp_servers.local.json").exists() and
                         "fake-test" in (self.home / "config" / "mcp_servers.local.json").read_text(encoding="utf-8"))

    def test_discover_rejects_external_and_reports_connection_failure(self):
        self.assertEqual(self.client.post("/api/admin/mcp/discover", json={"url": "http://example.com/sse"}).status_code, 400)
        self.assertEqual(self.client.post("/api/admin/mcp/discover", json={"url": "ftp://127.0.0.1/x"}).status_code, 422)
        out = self.client.post("/api/admin/mcp/discover", json={"url": "http://127.0.0.1:1/sse", "timeout_s": 5}).json()
        self.assertFalse(out["ok"])
        self.assertTrue(out["error"])

    def test_register_validation_then_success_and_delete(self):
        sel = lambda **k: {"name": "fetch_page", "risk": "write", "scope_args": {"url": "url"}, **k}  # noqa: E731
        self.assertEqual(self._reg([sel()], confirm=False).status_code, 400)
        self.assertEqual(self.client.post("/api/admin/mcp/burp/register", json={"url": self.url, "tools": [sel()], "confirm": True}).status_code, 400)  # 공유 서버 덮어쓰기
        self.assertEqual(self._reg([]).status_code, 422)
        self.assertEqual(self._reg([sel(name="nope")]).status_code, 422)  # 서버에 없는 도구
        self.assertEqual(self._reg([sel(risk="root")]).status_code, 422)
        self.assertEqual(self._reg([sel(scope_args={"depth": "url"})]).status_code, 422)  # 문자열 인자가 아님
        self.assertEqual(self._reg([{"name": "read_note", "risk": "read"}]).status_code, 400)  # 범위 검사 없는 read
        self.assertEqual(self._reg([{"name": "read_note", "risk": "read"}], confirm_unscoped_read=True).status_code, 200)
        rows = self._reg([sel()]).json()
        row = next(r for r in rows if r["name"] == "fake-test")
        self.assertEqual(row["source"], "local")
        self.assertEqual(row["tools"], ["fetch_page"])
        saved = json.loads((self.home / "config" / "mcp_servers.local.json").read_text(encoding="utf-8"))["servers"]["fake-test"]
        self.assertEqual(saved["allowed_tools"]["fetch_page"]["scope_args"], {"url": "url"})
        self.assertIn("url", saved["allowed_tools"]["fetch_page"]["args_schema"]["properties"])  # 서버가 알려 준 스키마
        spec = wb_runs.registry(self.deps.get_cfg())["mcp:fake-test/fetch_page"]
        self.assertEqual((spec.risk, spec.scope_args), ("write", {"url": "url"}))
        ev = self.conn.execute("SELECT details FROM audit_events WHERE event='admin_mcp_registered' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(ev["details"]["tools"]["fetch_page"]["risk"], "write")
        self.assertEqual(self.client.post("/api/admin/mcp/fake-test/delete", json={}).status_code, 400)
        self.assertNotIn("fake-test", [r["name"] for r in self.client.post("/api/admin/mcp/fake-test/delete", json={"confirm": True}).json()])
        self.assertEqual(self.client.post("/api/admin/mcp/burp/delete", json={"confirm": True}).status_code, 404)  # 이 PC 전용이 아님


if __name__ == "__main__":
    unittest.main()
