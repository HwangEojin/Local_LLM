"""자동 점검 (B) 발견 승격을 실제 PostgreSQL 로 end-to-end 확인: worker._promote_exploration_finding 이
CSRF/SSRF/SSTI/XXE/CMDI 신호가 일치할 때 findings·finding_evidence·finding 전용 노드·proves 엣지를
전부 만들고, 불일치할 때는 아무것도 안 만드는지 본다(SQLi/XSS 는 이미 확인된 범용 배선을 그대로 탄다).

증적·감사 이벤트 테이블은 추가 전용(DELETE 트리거로 막힘)이라 commit 한 적을 지울 수 없다. 그래서 전체를
한 트랜잭션에서 실행하고 끝에 rollback 한다 — 실제 DB 에 아무것도 남기지 않는다. DB 가 없거나
미마이그레이션이면 건너뛴다.
"""
import types
import unittest

from psycopg.types.json import Jsonb

from audit.models import ConfigError
from diagnostic_workbench import config, db, worker

_PAYLOADS = {
    "CSRF": ({"request": "POST /account/email HTTP/1.1\r\nHost: victim.example\r\n"
                         "Cookie: session=abc123\r\nOrigin: https://attacker.example\r\n\r\nemail=x"},
            b"HTTP/1.1 302 Found\r\nLocation: /account\r\n\r\n"),
    "SSRF": ({"url": "http://169.254.169.254/latest/meta-data/"}, b'{"iam/security-credentials": []}'),
    "SSTI": ({"name": "{{7*7}}"}, b"hello 49"),
    "XXE": ({"body": '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><foo>&xxe;</foo>'},
           b"<foo>root:x:0:0:root:/root:/bin/bash</foo>"),
    "CMDI": ({"name": "test; id"}, b"uid=33(www-data) gid=33(www-data)"),
}


class SignalPromotionDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cfg = config.load(None)
            cls.conn = db.connect(cfg.dsn)
            db.require_migrated(cls.conn, cfg.migrations_dir)
        except ConfigError as e:
            raise unittest.SkipTest(f"DB 연결/마이그레이션 불가: {e}") from None
        cv = cls.conn.execute("SELECT id FROM checklist_versions LIMIT 1").fetchone()
        if cv is None:
            cls.conn.rollback()
            raise unittest.SkipTest("checklist_versions 에 행이 없음 — 체크리스트 import 가 먼저 필요")
        # commit 하지 않는다: evidence/audit_events 는 추가 전용이라 지울 수 없다 — 끝에서 rollback 해서
        # 이 테스트가 만든 어떤 행도 실제 DB 에 남기지 않는다.
        cls.run_id = cls.conn.execute(
            "INSERT INTO runs (engagement_id, operator, checklist_version_id, scope, budget) "
            "VALUES ('TEST-SIGNAL-PROMOTION', 'pytest', %s, %s, %s) RETURNING id",
            (cv["id"], Jsonb({}), Jsonb({}))).fetchone()["id"]
        cls.asset = cls.conn.execute(
            "INSERT INTO assets (kind, value, source) VALUES ('host', 'signal-promotion-test.invalid', 'operator') "
            "ON CONFLICT (kind, value) DO UPDATE SET value = EXCLUDED.value RETURNING id").fetchone()["id"]

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.rollback()
            cls.conn.close()

    def _promote(self, args, body):
        fact = self.conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'fact','worker','{}') "
            "RETURNING id", (self.run_id,)).fetchone()["id"]
        eid = self.conn.execute(
            "INSERT INTO evidence (run_id, asset_id, requirement_id, tool, args, success, rel_path, sha256, size) "
            "VALUES (%s,%s,'test','test.tool','{}',true,'x','0', 0) RETURNING id",
            (self.run_id, self.asset)).fetchone()["id"]
        ctx = types.SimpleNamespace(conn=self.conn, run={"id": self.run_id})
        it = {"asset_id": self.asset}
        worker._promote_exploration_finding(ctx, it, fact, eid, args, body)
        return fact

    def test_matching_signal_creates_finding_for_each_new_class(self):
        for vclass, (args, body) in _PAYLOADS.items():
            with self.subTest(vclass=vclass):
                fact = self._promote(args, body)
                finding = self.conn.execute(
                    "SELECT f.id, f.verification FROM exploration_edges e "
                    "JOIN exploration_anchors a ON a.node_id = e.dst_id "
                    "JOIN findings f ON f.id = a.finding_id WHERE e.src_id = %s AND e.rel = 'proves'",
                    (fact,)).fetchone()
                self.assertIsNotNone(finding, f"{vclass}: proves 엣지로 연결된 finding 이 없음")
                self.assertEqual(finding["verification"], "RULE_CONFIRMED")
                ev_count = self.conn.execute(
                    "SELECT count(*) AS n FROM finding_evidence WHERE finding_id = %s", (finding["id"],)
                ).fetchone()["n"]
                self.assertEqual(ev_count, 1, f"{vclass}: finding_evidence 가 연결되지 않음")

    def test_non_matching_signal_creates_no_finding(self):
        fact = self._promote({"url": "http://169.254.169.254/"}, b"<html>nothing leaked here</html>")
        n = self.conn.execute(
            "SELECT count(*) AS n FROM exploration_edges WHERE src_id = %s AND rel = 'proves'", (fact,)
        ).fetchone()["n"]
        self.assertEqual(n, 0)
        audited = self.conn.execute(
            "SELECT details FROM audit_events WHERE event = 'exploration_signal_checked' "
            "AND details->>'fact' = %s", (str(fact),)).fetchone()
        self.assertIsNotNone(audited, "불일치해도 exploration_signal_checked 감사 이벤트가 남아야 함")
        self.assertFalse(audited["details"]["matched"])


if __name__ == "__main__":
    unittest.main()
