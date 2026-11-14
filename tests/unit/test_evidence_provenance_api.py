"""G5-a: 증거 출처 체인(traces.get_evidence_provenance, UPGRADE_PLAN.md)을 확인한다.

- 탐색(origin=llm)/트리거(origin=trigger)/재검증(origin=retest) 경로 모두 evidence → tool_invocation →
  backlog_item → exploration_node(들, goal 까지)로 체인이 끊기지 않는다.
- origin=llm 인 노드만 rationale 을 inferred 로 붙인다. 그 외(목표·트리거·재검증)는 observed.
- finding_evidence 로 연결된 finding 은 verification 에 따라 observed/inferred 로 나뉜다.
- 탐색 결정과 무관한 증거(예: 프록시로 받은 트래픽, backlog_item_id 없음)는 체인이 비어 있을 뿐 오류가 아니다.
"""
import json
import unittest

from psycopg.types.json import Jsonb

from audit.models import ConfigError
from diagnostic_workbench import config, db, evidence, runs, traces


class EvidenceProvenanceTest(unittest.TestCase):
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

    def _run_and_asset(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-evidence-provenance",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        asset_id = self.conn.execute(
            "SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        goal_id = self.conn.execute("SELECT id FROM exploration_nodes WHERE run_id=%s AND kind='goal'",
                                    (run["id"],)).fetchone()["id"]
        return run, asset_id, goal_id

    def _intent_backlog_evidence(self, run_id, asset_id, goal_id, origin, payload):
        node = self.conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'intent',%s,%s) "
            "RETURNING id", (run_id, origin, Jsonb(payload))).fetchone()["id"]
        self.conn.execute("INSERT INTO exploration_edges VALUES (%s,%s,'spawns')", (goal_id, node))
        item = self.conn.execute(
            "INSERT INTO backlog_items (run_id, kind, exploration_node_id, asset_id, idempotency_key, reason) "
            "VALUES (%s,'exploration',%s,%s,%s,'테스트 작업 사유') RETURNING id",
            (run_id, node, asset_id, f"test:provenance:{node}")).fetchone()["id"]
        eid = evidence.store(self.conn, self.cfg.evidence_dir, run_id=run_id, asset_id=asset_id,
                             requirement_id="provenance-test", tool="test.fixture", args={},
                             backlog_item_id=item, error="고정된 테스트 오류(실제 도구 실행 없음)")
        self.conn.execute(
            "INSERT INTO tool_invocations (run_id, backlog_item_id, asset_id, step_no, tool, args, "
            "policy_decision, policy_reason, status, evidence_id) VALUES "
            "(%s,%s,%s,1,'test.fixture','{}','ALLOW','허용됨','SKIPPED',%s)",
            (run_id, item, asset_id, eid))
        return node, item, eid

    def test_exploration_llm_path_chain_unbroken(self):
        run, asset_id, goal_id = self._run_and_asset()
        node, item, eid = self._intent_backlog_evidence(
            run["id"], asset_id, goal_id, "llm",
            {"tool": "test.fixture", "args": {}, "rationale": "이전 관찰로 보아 이 경로가 의심됨"})
        result = traces.get_evidence_provenance(self.conn, run["id"], eid)
        self.assertIsNotNone(result)
        self.assertEqual(result["evidence"]["node_kind"], "observed")
        self.assertEqual(result["tool_invocation"]["node_kind"], "observed")
        self.assertEqual(result["backlog_item"]["reason"], "테스트 작업 사유")
        self.assertEqual([n["kind"] for n in result["exploration_path"]], ["goal", "intent"])
        self.assertEqual(result["exploration_path"][0]["node_kind"], "observed")  # 목표(운영자 선언)
        intent_entry = result["exploration_path"][1]
        self.assertEqual(intent_entry["origin"], "llm")
        self.assertEqual(intent_entry["node_kind"], "inferred")  # LLM 의 rationale 은 추론
        self.assertEqual(intent_entry["rationale"], "이전 관찰로 보아 이 경로가 의심됨")

    def test_trigger_path_chain_unbroken_and_observed(self):
        run, asset_id, goal_id = self._run_and_asset()
        node, item, eid = self._intent_backlog_evidence(
            run["id"], asset_id, goal_id, "trigger",
            {"trigger_id": 1, "trigger_name": "주기 재스캔", "tool": "test.fixture", "args": {}})
        result = traces.get_evidence_provenance(self.conn, run["id"], eid)
        self.assertEqual([n["kind"] for n in result["exploration_path"]], ["goal", "intent"])
        intent_entry = result["exploration_path"][1]
        self.assertEqual(intent_entry["origin"], "trigger")
        self.assertEqual(intent_entry["node_kind"], "observed")  # 규칙 기반 조건 발동 — 추론 아님
        self.assertNotIn("rationale", intent_entry)
        self.assertEqual(intent_entry["summary"], "주기 재스캔")

    def test_retest_path_chain_unbroken_and_observed(self):
        run, asset_id, goal_id = self._run_and_asset()
        node, item, eid = self._intent_backlog_evidence(
            run["id"], asset_id, goal_id, "retest",
            {"tool": "test.fixture", "args": {}, "retest_of_finding": "dummy"})
        result = traces.get_evidence_provenance(self.conn, run["id"], eid)
        self.assertEqual([n["kind"] for n in result["exploration_path"]], ["goal", "intent"])
        self.assertEqual(result["exploration_path"][1]["origin"], "retest")
        self.assertEqual(result["exploration_path"][1]["node_kind"], "observed")

    def test_findings_are_split_by_verification(self):
        run, asset_id, goal_id = self._run_and_asset()
        node, item, eid = self._intent_backlog_evidence(
            run["id"], asset_id, goal_id, "worker", {"tool": "test.fixture", "args": {}})
        rule_fid = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification) "
            "VALUES (%s,%s,'규칙 확정 발견','근거','대응','RULE_CONFIRMED') RETURNING id",
            (run["id"], asset_id)).fetchone()["id"]
        llm_fid = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification) "
            "VALUES (%s,%s,'LLM 확정 발견','근거','대응','LLM_CONFIRMED') RETURNING id",
            (run["id"], asset_id)).fetchone()["id"]
        for fid in (rule_fid, llm_fid):
            self.conn.execute("INSERT INTO finding_evidence VALUES (%s,%s)", (fid, eid))
        result = traces.get_evidence_provenance(self.conn, run["id"], eid)
        by_title = {f["title"]: f for f in result["findings"]}
        self.assertEqual(by_title["규칙 확정 발견"]["node_kind"], "observed")
        self.assertEqual(by_title["LLM 확정 발견"]["node_kind"], "inferred")

    def test_evidence_with_no_exploration_decision_returns_empty_chain(self):
        """proxy.py 로 받은 트래픽처럼 backlog_item 없이 저장된 증거도 오류 없이 빈 체인으로 돌아온다."""
        run, asset_id, _goal_id = self._run_and_asset()
        eid = evidence.store(self.conn, self.cfg.evidence_dir, run_id=run["id"], asset_id=asset_id,
                             requirement_id="proxy", tool="proxy.http", args={},
                             error="고정된 테스트 오류(실제 프록시 호출 없음)")
        result = traces.get_evidence_provenance(self.conn, run["id"], eid)
        self.assertIsNotNone(result)
        self.assertIsNone(result["tool_invocation"])
        self.assertIsNone(result["backlog_item"])
        self.assertEqual(result["exploration_path"], [])
        self.assertEqual(result["findings"], [])

    def test_unknown_evidence_id_returns_none(self):
        run, _asset_id, _goal_id = self._run_and_asset()
        result = traces.get_evidence_provenance(self.conn, run["id"], "00000000-0000-0000-0000-000000000000")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
