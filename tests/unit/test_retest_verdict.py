"""전용 재검증 판정(worker._resolve_retest)을 확인한다.

- 탐색 신호(signals.py)로 확정된 finding 의 재검증은 같은 신호로 REPRODUCED/FIXED 를 자동 판정한다.
- 체크리스트 규칙·LLM 확정 finding(결정론적으로 재확인할 신호가 없음)이나 도구 실행 실패는 INCONCLUSIVE.
- 어느 쪽이든 findings.status 는 바꾸지 않는다 — 판정은 finding_review_history 에 이력으로만 남는다.
"""
import json
import unittest

from psycopg.types.json import Jsonb

from audit.models import ConfigError, ToolOutput
from diagnostic_workbench import config, db, evidence, runs, worker


class ResolveRetestDB(unittest.TestCase):
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

    def _setup(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-retest-verdict",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        asset_id = self.conn.execute(
            "SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        ctx = runs.build_context(self.cfg, self.conn, run)
        return run, asset_id, ctx

    def _finding(self, run_id, asset_id, verification="RULE_CONFIRMED"):
        return self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, severity, rationale, remediation, verification) "
            "VALUES (%s,%s,'테스트 발견','high','신호 일치','패치',%s) RETURNING id",
            (run_id, asset_id, verification)).fetchone()["id"]

    def _retest_node(self, run_id, asset_id, finding_id, vclass, args):
        goal = self.conn.execute("SELECT id FROM exploration_nodes WHERE run_id=%s AND kind='goal'",
                                 (run_id,)).fetchone()["id"]
        node = self.conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'intent','retest',%s) "
            "RETURNING id", (run_id, Jsonb({"tool": "test.fixture", "args": args,
                                            "retest_of_finding": str(finding_id),
                                            "retest_vuln_class": vclass}))).fetchone()["id"]
        self.conn.execute("INSERT INTO exploration_edges VALUES (%s,%s,'spawns')", (goal, node))
        self.conn.execute("INSERT INTO exploration_anchors (node_id, asset_id) VALUES (%s,%s)", (node, asset_id))
        return node

    def _history(self, finding_id):
        return self.conn.execute(
            "SELECT * FROM finding_review_history WHERE finding_id=%s AND actor='system:retest' "
            "ORDER BY changed_at DESC LIMIT 1", (finding_id,)).fetchone()

    def _evidence(self, run_id, asset_id, out):
        return evidence.store(self.conn, self.cfg.evidence_dir, run_id=run_id, asset_id=asset_id,
                              requirement_id="retest", tool="test.fixture", args={}, output=out)

    def test_reproduced_when_signal_still_matches(self):
        run, asset_id, ctx = self._setup()
        fid = self._finding(run["id"], asset_id)
        args = {"q": "<script>alert(1)</script>"}
        node = self._retest_node(run["id"], asset_id, fid, "XSS", args)
        it = {"exploration_node_id": node}
        out = ToolOutput(b"<html>you searched: <script>alert(1)</script></html>", "test-1")
        eid = self._evidence(run["id"], asset_id, out)
        before = self.conn.execute("SELECT status FROM findings WHERE id=%s", (fid,)).fetchone()["status"]
        worker._resolve_retest(ctx, it, eid, args, out)
        h = self._history(fid)
        self.assertIn("REPRODUCED", h["reason"])
        after = self.conn.execute("SELECT status FROM findings WHERE id=%s", (fid,)).fetchone()["status"]
        self.assertEqual(before, after)  # 상태는 안 바뀜
        self.assertIsNotNone(self.conn.execute(
            "SELECT 1 FROM finding_evidence WHERE finding_id=%s AND evidence_id=%s", (fid, eid)).fetchone())

    def test_fixed_when_signal_no_longer_matches(self):
        run, asset_id, ctx = self._setup()
        fid = self._finding(run["id"], asset_id)
        args = {"q": "<script>alert(1)</script>"}
        node = self._retest_node(run["id"], asset_id, fid, "XSS", args)
        it = {"exploration_node_id": node}
        out = ToolOutput(b"<html>you searched: &lt;script&gt;alert(1)&lt;/script&gt;</html>", "test-1")
        eid = self._evidence(run["id"], asset_id, out)
        worker._resolve_retest(ctx, it, eid, args, out)
        h = self._history(fid)
        self.assertIn("FIXED", h["reason"])

    def test_inconclusive_when_tool_failed(self):
        run, asset_id, ctx = self._setup()
        fid = self._finding(run["id"], asset_id)
        args = {"q": "<script>alert(1)</script>"}
        node = self._retest_node(run["id"], asset_id, fid, "XSS", args)
        it = {"exploration_node_id": node}
        worker._resolve_retest(ctx, it, None, args, None)
        h = self._history(fid)
        self.assertIn("INCONCLUSIVE", h["reason"])

    def test_inconclusive_when_no_deterministic_signal(self):
        run, asset_id, ctx = self._setup()
        fid = self._finding(run["id"], asset_id, verification="LLM_CONFIRMED")
        args = {"q": "hello"}
        node = self._retest_node(run["id"], asset_id, fid, None, args)
        it = {"exploration_node_id": node}
        out = ToolOutput(b"<html>hello</html>", "test-1")
        worker._resolve_retest(ctx, it, None, args, out)
        h = self._history(fid)
        self.assertIn("INCONCLUSIVE", h["reason"])

    def test_non_retest_intent_is_ignored(self):
        run, asset_id, ctx = self._setup()
        goal = self.conn.execute("SELECT id FROM exploration_nodes WHERE run_id=%s AND kind='goal'",
                                 (run["id"],)).fetchone()["id"]
        node = self.conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'intent','llm','{}') "
            "RETURNING id", (run["id"],)).fetchone()["id"]
        self.conn.execute("INSERT INTO exploration_edges VALUES (%s,%s,'spawns')", (goal, node))
        it = {"exploration_node_id": node}
        worker._resolve_retest(ctx, it, None, {}, None)  # 조용히 아무 일도 안 함(예외 없음)


if __name__ == "__main__":
    unittest.main()
