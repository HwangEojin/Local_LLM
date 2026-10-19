"""SQLi 불린 기반 차등 검증을 실제 PostgreSQL + 실제 증적 파일로 end-to-end 확인: 참/거짓 조건의 증적을
각각 저장해 두고, worker._promote_exploration_finding 이 둘을 찾아 비교해서 응답 길이가 의미 있게
다르면(SQL 오류 문구가 전혀 없어도) finding 으로 승격하는지 본다.

evidence/audit_events 는 추가 전용이라 commit 하지 않고 끝에서 rollback 한다(test_signal_promotion_db.py
와 같은 패턴). 단, evidence 파일은 트랜잭션과 무관하게 디스크에 남으므로 임시 디렉터리에 쓰고 지운다.
"""
import tempfile
import types
import unittest
from pathlib import Path

from psycopg.types.json import Jsonb

from audit.models import ConfigError, ToolOutput
from diagnostic_workbench import config, db, evidence, worker


class SqliBooleanDiffDB(unittest.TestCase):
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
            raise unittest.SkipTest("checklist_versions 에 행이 없음")
        cls.run_id = cls.conn.execute(
            "INSERT INTO runs (engagement_id, operator, checklist_version_id, scope, budget) "
            "VALUES ('TEST-SQLI-BOOLEAN-DIFF', 'pytest', %s, %s, %s) RETURNING id",
            (cv["id"], Jsonb({}), Jsonb({}))).fetchone()["id"]
        cls.asset = cls.conn.execute(
            "INSERT INTO assets (kind, value, source) VALUES ('host', 'sqli-boolean-diff-test.invalid', "
            "'operator') ON CONFLICT (kind, value) DO UPDATE SET value = EXCLUDED.value RETURNING id"
        ).fetchone()["id"]
        cls._tmp = tempfile.TemporaryDirectory()
        cls.evidence_dir = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()  # evidence 파일은 트랜잭션과 무관하므로 직접 지운다
        if hasattr(cls, "conn"):
            cls.conn.rollback()
            cls.conn.close()

    def _store(self, args, body: bytes):
        return evidence.store(self.conn, self.evidence_dir, run_id=self.run_id, asset_id=self.asset,
                              requirement_id="explore", tool="mcp:burp/send_http1_request", args=args,
                              output=ToolOutput(body, "test/1"))

    def _promote(self, args, body):
        fact = self.conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'fact','worker','{}') "
            "RETURNING id", (self.run_id,)).fetchone()["id"]
        eid = self._store(args, body)
        ctx = types.SimpleNamespace(conn=self.conn, run={"id": self.run_id},
                                    cfg=types.SimpleNamespace(evidence_dir=self.evidence_dir))
        it = {"asset_id": self.asset}
        worker._promote_exploration_finding(ctx, it, fact, eid, args, body)
        return fact

    def test_true_false_pair_with_different_lengths_is_promoted(self):
        false_args = {"q": "1' AND '1'='2", "page": "2"}
        self._promote(false_args, b"<html>no results found</html>")  # 참보다 먼저 기록된 '거짓' 짝

        true_args = {"q": "1' AND '1'='1", "page": "2"}
        fact = self._promote(true_args, b"<html>" + b"result row " * 50 + b"</html>")

        finding = self.conn.execute(
            "SELECT f.id, f.title, f.rationale FROM exploration_edges e "
            "JOIN exploration_anchors a ON a.node_id = e.dst_id JOIN findings f ON f.id = a.finding_id "
            "WHERE e.src_id = %s AND e.rel = 'proves'", (fact,)).fetchone()
        self.assertIsNotNone(finding, "참/거짓 응답 길이가 크게 다른데도 finding 이 안 생김")
        self.assertIn("SQLI", finding["title"])
        self.assertIn("불린 기반", finding["rationale"])
        ev_count = self.conn.execute(
            "SELECT count(*) AS n FROM finding_evidence WHERE finding_id = %s", (finding["id"],)).fetchone()["n"]
        self.assertEqual(ev_count, 2, "참/거짓 증적 둘 다 finding_evidence 로 연결돼야 함")

    def test_pair_with_identical_responses_is_not_promoted(self):
        false_args = {"q": "1' AND '1'='2", "page": "9"}
        self._promote(false_args, b"<html>5 results</html>")

        true_args = {"q": "1' AND '1'='1", "page": "9"}
        fact = self._promote(true_args, b"<html>5 results</html>")

        n = self.conn.execute(
            "SELECT count(*) AS n FROM exploration_edges WHERE src_id = %s AND rel = 'proves'", (fact,)
        ).fetchone()["n"]
        self.assertEqual(n, 0)

    def test_unpaired_boolean_probe_falls_through_without_crashing(self):
        # 짝(반대 조건)이 아직 없으면 차등 검증은 조용히 건너뛰고(None), 일반 오류 기반 신호만 시도한다.
        fact = self._promote({"q": "1' AND '1'='1", "page": "unique-no-sibling"},
                             b"<html>no error here</html>")
        n = self.conn.execute(
            "SELECT count(*) AS n FROM exploration_edges WHERE src_id = %s AND rel = 'proves'", (fact,)
        ).fetchone()["n"]
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
