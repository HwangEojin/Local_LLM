"""Finding 확정 알림(notify.py)을 확인한다.

- load_channels: 파일이 없으면 빈 목록, enabled=false 는 걸러짐.
- _post: 실제 로컬 HTTP 서버로 성공/실패 상태 코드를 구분하는지(mock 아닌 실 소켓).
- notify_finding_confirmed: 실제 PostgreSQL 에 audit_events 로 성공/실패가 남는지, 채널이 없으면
  아무 일도 하지 않는지(알림은 부수 효과일 뿐 판정을 막지 않음).
"""
import json
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from audit.models import ConfigError
from diagnostic_workbench import config, db, notify, runs


class _Handler(BaseHTTPRequestHandler):
    status = 200

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.received = self.rfile.read(length)
        self.send_response(self.status)
        self.end_headers()

    def log_message(self, *a):
        pass


class _Server:
    def __init__(self, status=200):
        self.httpd = HTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.RequestHandlerClass.status = status
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self):
        return f"http://127.0.0.1:{self.port}/hook"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class LoadChannels(unittest.TestCase):
    def test_missing_file_means_no_channels(self):
        self.assertEqual(notify.load_channels(Path("/no/such/notify.json")), [])

    def test_disabled_channel_is_filtered_out(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "notify.json"
            p.write_text(json.dumps({"channels": [
                {"name": "on", "kind": "webhook", "url": "http://x", "enabled": True},
                {"name": "off", "kind": "webhook", "url": "http://x", "enabled": False},
            ]}), encoding="utf-8")
            loaded = notify.load_channels(p)
            self.assertEqual([c["name"] for c in loaded], ["on"])

    def test_rejects_unknown_kind(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "notify.json"
            p.write_text(json.dumps({"channels": [{"name": "x", "kind": "email", "url": "a@b.com"}]}),
                        encoding="utf-8")
            with self.assertRaises(ConfigError):
                notify.load_channels(p)


class PostToRealServer(unittest.TestCase):
    def test_success_status_reported_ok(self):
        srv = _Server(status=200)
        try:
            detail, ok = notify._post(srv.url(), b'{"a":1}')
            self.assertTrue(ok)
            self.assertEqual(detail, "HTTP 200")
        finally:
            srv.close()

    def test_server_error_status_reported_not_ok(self):
        srv = _Server(status=500)
        try:
            detail, ok = notify._post(srv.url(), b'{}')
            self.assertFalse(ok)
        finally:
            srv.close()

    def test_connection_refused_reported_not_ok(self):
        detail, ok = notify._post("http://127.0.0.1:1/hook", b"{}")
        self.assertFalse(ok)


class NotifyFindingConfirmedDB(unittest.TestCase):
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

    def _run_and_finding(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-notify",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        asset_id = self.conn.execute(
            "SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        f = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, severity, rationale, remediation, verification) "
            "VALUES (%s,%s,'테스트 발견','high','신호 일치','패치','RULE_CONFIRMED') RETURNING id",
            (run["id"], asset_id)).fetchone()
        return run, f["id"]

    def test_no_channel_file_does_nothing(self):
        run, fid = self._run_and_finding()
        fake_cfg = types.SimpleNamespace(home=Path(tempfile.mkdtemp()))
        before = self.conn.execute("SELECT count(*) AS n FROM audit_events WHERE run_id=%s", (run["id"],)).fetchone()["n"]
        notify.notify_finding_confirmed(self.conn, fake_cfg, run["id"], fid, title="t", severity="high", actor="test")
        after = self.conn.execute("SELECT count(*) AS n FROM audit_events WHERE run_id=%s", (run["id"],)).fetchone()["n"]
        self.assertEqual(before, after)

    def test_successful_delivery_logged_in_audit_events(self):
        run, fid = self._run_and_finding()
        srv = _Server(status=200)
        try:
            tmp = Path(tempfile.mkdtemp())
            (tmp / "config").mkdir()
            (tmp / "config" / "notify.json").write_text(
                json.dumps({"channels": [{"name": "test-ch", "kind": "webhook", "url": srv.url(), "enabled": True}]}),
                encoding="utf-8")
            fake_cfg = types.SimpleNamespace(home=tmp)
            notify.notify_finding_confirmed(self.conn, fake_cfg, run["id"], fid, title="t", severity="high",
                                            actor="test")
            row = self.conn.execute(
                "SELECT event, details FROM audit_events WHERE run_id=%s AND event='notification_sent'",
                (run["id"],)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["details"]["channel"], "test-ch")
        finally:
            srv.close()

    def test_min_severity_filters_lower_severity_findings(self):
        run, fid = self._run_and_finding()
        srv = _Server(status=200)
        try:
            tmp = Path(tempfile.mkdtemp())
            (tmp / "config").mkdir()
            (tmp / "config" / "notify.json").write_text(
                json.dumps({"channels": [{"name": "high-only", "kind": "webhook", "url": srv.url(),
                                          "enabled": True, "min_severity": "high"}]}), encoding="utf-8")
            fake_cfg = types.SimpleNamespace(home=tmp)
            notify.notify_finding_confirmed(self.conn, fake_cfg, run["id"], fid, title="t", severity="low",
                                            actor="test")
            sent = self.conn.execute(
                "SELECT 1 FROM audit_events WHERE run_id=%s AND event IN ('notification_sent','notification_failed')",
                (run["id"],)).fetchone()
            self.assertIsNone(sent)  # low < high 라 채널에 안 걸림, 아무 전송도 시도 안 함

            notify.notify_finding_confirmed(self.conn, fake_cfg, run["id"], fid, title="t", severity="critical",
                                            actor="test")
            row = self.conn.execute(
                "SELECT details FROM audit_events WHERE run_id=%s AND event='notification_sent'",
                (run["id"],)).fetchone()
            self.assertIsNotNone(row)  # critical >= high 라 걸림
            self.assertEqual(row["details"]["severity"], "critical")
        finally:
            srv.close()


class MinSeverityFilter(unittest.TestCase):
    def test_passes_severity_ranking_and_unknown_values(self):
        no_floor = {"name": "x"}
        floor_high = {"name": "x", "min_severity": "high"}
        self.assertTrue(notify._passes_severity(no_floor, "info"))
        self.assertTrue(notify._passes_severity(floor_high, "critical"))
        self.assertTrue(notify._passes_severity(floor_high, "high"))
        self.assertFalse(notify._passes_severity(floor_high, "medium"))
        self.assertTrue(notify._passes_severity(floor_high, "risk-3"))  # 모르는 값은 거르지 않음(놓치는 것보다 나음)


if __name__ == "__main__":
    unittest.main()
