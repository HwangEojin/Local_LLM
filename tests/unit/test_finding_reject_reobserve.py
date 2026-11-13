"""G1 D3: REJECTED 된 finding 이 같은 실행에서 다시 관찰될 때의 재오픈 규칙을 확인한다(UPGRADE_PLAN.md).

- 기각될 때 없던 '종류'의 근거(RULE/LLM)로 재관찰되면 NEEDS_REVIEW 로 재오픈하고 알림 1회.
- 기각될 때 이미 봤던 종류로 재관찰되면 REJECTED 유지, 알림 없음(이력·감사 이벤트만).
- 재오픈 후 다시 기각되면, 그 뒤로는 RULE/LLM 어느 쪽 재관찰에도 다시 열리지 않는다(둘 다 evidence_kinds 에
  있으므로 — 같은 오탐이 기각할 때마다 또 열리는 루프 방지).
"""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from audit.models import ConfigError
from diagnostic_workbench import config, db, runs, worker


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.received = self.rfile.read(length)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


class _Server:
    def __init__(self):
        self.httpd = HTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self):
        return f"http://127.0.0.1:{self.port}/hook"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FindingRejectReobserve(unittest.TestCase):
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

    def setUp(self):
        self._notify_home = tempfile.mkdtemp()
        self.srv = _Server()
        self.addCleanup(self.srv.close)
        (Path(self._notify_home) / "config").mkdir()
        (Path(self._notify_home) / "config" / "notify.json").write_text(
            json.dumps({"channels": [{"name": "test-ch", "kind": "webhook", "url": self.srv.url(), "enabled": True}]}),
            encoding="utf-8")
        import types
        self.notify_cfg = types.SimpleNamespace(home=Path(self._notify_home))

    def _run_and_asset(self, operator):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], operator, checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        asset_id = self.conn.execute(
            "SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        return run, asset_id

    def _evidence(self, run_id, asset_id):
        return self.conn.execute(
            "INSERT INTO evidence (run_id, asset_id, requirement_id, tool, args, success, rel_path, sha256, size) "
            "VALUES (%s,%s,'TEST-REOBS','test.manual','{}',true,'test/fake.txt',repeat('a',64),0) RETURNING id",
            (run_id, asset_id)).fetchone()["id"]

    def _upsert(self, run_id, asset_id, args, evidence_kind, evidence_ids):
        def insert(conn):
            return conn.execute(
                "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification) "
                "VALUES (%s,%s,'재관찰 테스트','근거','조치','RULE_CONFIRMED') RETURNING id", (run_id, asset_id)
            ).fetchone()["id"]
        return worker.upsert_exploration_finding(self.conn, self.notify_cfg, run_id, asset_id, vuln_class="SQLI",
                                                  args=args, evidence_kind=evidence_kind, insert_fn=insert,
                                                  evidence_ids=evidence_ids, actor="worker")

    def _audit_count(self, run_id, event, finding_id):
        return self.conn.execute(
            "SELECT count(*) AS n FROM audit_events WHERE run_id=%s AND event=%s AND details->>'finding_id'=%s",
            (run_id, event, str(finding_id))).fetchone()["n"]

    def _notify_count(self, run_id, finding_id):
        return self.conn.execute(
            "SELECT count(*) AS n FROM audit_events WHERE run_id=%s AND event='notification_sent' "
            "AND details->>'finding_id'=%s", (run_id, str(finding_id))).fetchone()["n"]

    def test_llm_finding_rejected_then_rule_reobserve_reopens(self):
        run, asset_id = self._run_and_asset("pytest-reobs-llm-rule")
        args = {"url": "http://x/reobs1?u=' OR '1'='1"}
        eid1 = self._evidence(run["id"], asset_id)
        fid, created = self._upsert(run["id"], asset_id, args, "LLM", [eid1])
        self.assertTrue(created)
        worker.set_finding_status(self.conn, fid, "REJECTED", "오탐으로 판단", actor="reviewer:pytest")

        eid2 = self._evidence(run["id"], asset_id)
        fid2, created2 = self._upsert(run["id"], asset_id, args, "RULE", [eid2])
        self.assertFalse(created2)
        self.assertEqual(fid, fid2)
        row = self.conn.execute("SELECT status FROM findings WHERE id=%s", (fid,)).fetchone()
        self.assertEqual(row["status"], "NEEDS_REVIEW")
        self.assertEqual(self._audit_count(run["id"], "finding_reopened_new_evidence_kind", fid), 1)
        self.assertEqual(self._notify_count(run["id"], fid), 1)

    def test_rule_finding_rejected_then_same_rule_reobserve_stays_rejected(self):
        run, asset_id = self._run_and_asset("pytest-reobs-rule-rule")
        args = {"url": "http://x/reobs2?u=' OR '1'='1"}
        eid1 = self._evidence(run["id"], asset_id)
        fid, created = self._upsert(run["id"], asset_id, args, "RULE", [eid1])
        self.assertTrue(created)
        worker.set_finding_status(self.conn, fid, "REJECTED", "오탐으로 판단", actor="reviewer:pytest")

        eid2 = self._evidence(run["id"], asset_id)
        fid2, created2 = self._upsert(run["id"], asset_id, args, "RULE", [eid2])
        self.assertFalse(created2)
        row = self.conn.execute("SELECT status FROM findings WHERE id=%s", (fid,)).fetchone()
        self.assertEqual(row["status"], "REJECTED")  # 같은 종류라 재오픈 안 함
        self.assertEqual(self._audit_count(run["id"], "finding_reobserved_after_reject", fid), 1)
        self.assertEqual(self._notify_count(run["id"], fid), 0)  # 알림 없음

    def test_reopened_then_rejected_again_does_not_reopen_on_either_kind(self):
        run, asset_id = self._run_and_asset("pytest-reobs-loop")
        args = {"url": "http://x/reobs3?u=' OR '1'='1"}
        eid1 = self._evidence(run["id"], asset_id)
        fid, _ = self._upsert(run["id"], asset_id, args, "LLM", [eid1])
        worker.set_finding_status(self.conn, fid, "REJECTED", "오탐1", actor="reviewer:pytest")

        eid2 = self._evidence(run["id"], asset_id)
        self._upsert(run["id"], asset_id, args, "RULE", [eid2])  # 재오픈(NEEDS_REVIEW), evidence_kinds={LLM,RULE}
        worker.set_finding_status(self.conn, fid, "REJECTED", "다시 오탐 확인", actor="reviewer:pytest")

        for kind in ("LLM", "RULE"):
            eid = self._evidence(run["id"], asset_id)
            self._upsert(run["id"], asset_id, args, kind, [eid])
            row = self.conn.execute("SELECT status FROM findings WHERE id=%s", (fid,)).fetchone()
            self.assertEqual(row["status"], "REJECTED", f"{kind} 재관찰로 다시 열리면 안 됨")
        self.assertEqual(self._audit_count(run["id"], "finding_reopened_new_evidence_kind", fid), 1)  # 최초 1번뿐


if __name__ == "__main__":
    unittest.main()
