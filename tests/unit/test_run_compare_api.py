"""G6: 실행 간 비교(compare.compare_runs, /api/compare, UPGRADE_PLAN.md)를 확인한다(H2 규칙 포함).

- 신규/지속/재발/미재현(조치 추정)/미확인 5종 분류와, fingerprint 가 NULL 인 finding 은 "비교 불가"로
  따로 빠지는지(자동 분류하지 않음).
- "미재현(조치 추정)"은 반드시 (head 가 실제로 같은 지점을 때렸는데 신호가 안 뜸) 또는 (FIXED 재검증
  이력이 있음) 중 하나일 때만 — 그냥 head 에 없다고 조치됐다고 단정하지 않는다(CLAUDE.md §15).
- 두 실행의 자산 company 가 다르면 /api/compare 가 422 를 돌려준다.
"""
import unittest

from psycopg.types.json import Jsonb

from audit.models import ConfigError
from diagnostic_workbench import compare, config, db, fingerprint, runs


class RunCompareTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg = config.load(None)
            cls.conn = db.connect(cls.cfg.dsn)
            db.require_migrated(cls.conn, cls.cfg.migrations_dir)
        except ConfigError as e:
            raise unittest.SkipTest(f"DB 연결/마이그레이션 불가: {e}") from None
        if "baseline-demo" not in {t["target_id"] for t in __import__("json").loads(
                (cls.cfg.home / "config" / "scope.json").read_text(encoding="utf-8"))["targets"]}:
            raise unittest.SkipTest("scope.json 에 baseline-demo 대상이 없음")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def _run(self, operator="pytest-compare"):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], operator, checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        return run

    def _asset(self, run):
        return self.conn.execute("SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]

    # H1(v2): fingerprint 는 실제 주입 매개변수가 있어야 만들어진다 — 픽스처도 페이로드가 든 매개변수(q)를 쓴다.
    PAYLOAD = "?q=' OR '1'='1"

    def _finding(self, run_id, asset_id, path, *, status="CONFIRMED", vuln_class="SQLI"):
        fp, loc_hash, locator = fingerprint.compute(asset_id, vuln_class,
                                                    {"url": f"http://localhost{path}{self.PAYLOAD}"})
        f = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification, status) "
            "VALUES (%s,%s,%s,'근거','대응','RULE_CONFIRMED',%s) RETURNING *",
            (run_id, asset_id, f"비교 테스트 {path}", status)).fetchone()
        self.conn.execute(
            "UPDATE findings SET vuln_class=%s, fingerprint=%s, fingerprint_v=%s, locator=%s, locator_hash=%s "
            "WHERE id=%s", (vuln_class, fp, fingerprint.FINGERPRINT_VERSION, Jsonb(locator), loc_hash, f["id"]))
        return self.conn.execute("SELECT * FROM findings WHERE id=%s", (f["id"],)).fetchone()

    def _mark_fixed_retest(self, finding_id):
        self.conn.execute(
            "INSERT INTO finding_review_history (finding_id, old_status, new_status, reason, actor) "
            "VALUES (%s,'CONFIRMED','CONFIRMED','재검증 결과: FIXED — 신호가 더는 뜨지 않음','system:retest')",
            (finding_id,))

    def _evidence_at(self, run_id, asset_id, path):
        return self.conn.execute(
            "INSERT INTO evidence (run_id, asset_id, requirement_id, tool, args, success, rel_path, sha256, size) "
            "VALUES (%s,%s,'compare-test','test.fixture',%s,true,'x','x',1) RETURNING id",
            (run_id, asset_id, Jsonb({"url": f"http://localhost{path}{self.PAYLOAD}"}))).fetchone()["id"]

    def _checked_evidence(self, run_id, asset_id, url, vuln_class, *, matched=False):
        """head 가 실제로 요청을 보내고 결정론적 신호 판정까지 한 흔적(worker._run_exploration 과 같은 모양):
        evidence → fact 노드(exploration_anchors) → audit_events.exploration_signal_checked."""
        eid = self.conn.execute(
            "INSERT INTO evidence (run_id, asset_id, requirement_id, tool, args, success, rel_path, sha256, size) "
            "VALUES (%s,%s,'compare-test','test.fixture',%s,true,'x','x',1) RETURNING id",
            (run_id, asset_id, Jsonb({"url": url}))).fetchone()["id"]
        fact = self.conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, state, origin, payload) "
            "VALUES (%s,'fact','RECORDED','worker','{}') RETURNING id", (run_id,)).fetchone()["id"]
        self.conn.execute("INSERT INTO exploration_anchors (node_id, evidence_id) VALUES (%s,%s)", (fact, eid))
        db.audit(self.conn, run_id, "exploration_signal_checked",
                 {"fact": str(fact), "vuln_class": vuln_class, "matched": matched, "reason": "test"})
        return eid

    def test_new_classification(self):
        base, head = self._run(), self._run()
        asset = self._asset(base)
        self._asset(head)  # 같은 자산(baseline-demo) 확인용, 반환값은 안 씀
        hf = self._finding(head["id"], asset, "/new-path")
        result = compare.compare_runs(self.conn, base["id"], head["id"])
        self.assertIn(hf["id"], [x["id"] for x in result["new"]])

    def test_persistent_classification(self):
        base, head = self._run(), self._run()
        asset = self._asset(base)
        bf = self._finding(base["id"], asset, "/persistent-path")
        hf = self._finding(head["id"], asset, "/persistent-path")
        result = compare.compare_runs(self.conn, base["id"], head["id"])
        pairs = [(p["base"]["id"], p["head"]["id"]) for p in result["persistent"]]
        self.assertIn((bf["id"], hf["id"]), pairs)
        self.assertEqual(result["recurred"], [])

    def test_recurred_classification(self):
        base, head = self._run(), self._run()
        asset = self._asset(base)
        bf = self._finding(base["id"], asset, "/recurred-path")
        self._mark_fixed_retest(bf["id"])
        hf = self._finding(head["id"], asset, "/recurred-path")
        result = compare.compare_runs(self.conn, base["id"], head["id"])
        pairs = [(p["base"]["id"], p["head"]["id"]) for p in result["recurred"]]
        self.assertIn((bf["id"], hf["id"]), pairs)
        self.assertNotIn((bf["id"], hf["id"]), [(p["base"]["id"], p["head"]["id"]) for p in result["persistent"]])

    def test_rejected_findings_are_set_aside_not_counted(self):
        """H2: 기각(REJECTED)된 오탐은 신규·지속·재발 집계에 들어가지 않고 rejected 묶음으로 따로 나온다."""
        base, head = self._run(), self._run()
        asset = self._asset(base)
        bf = self._finding(base["id"], asset, "/rejected-in-base", status="REJECTED")
        self._mark_fixed_retest(bf["id"])
        hf = self._finding(head["id"], asset, "/rejected-in-base")
        hr = self._finding(head["id"], asset, "/rejected-in-head", status="REJECTED")
        result = compare.compare_runs(self.conn, base["id"], head["id"])
        self.assertIn(bf["id"], [x["id"] for x in result["rejected"]["base"]])
        self.assertIn(hr["id"], [x["id"] for x in result["rejected"]["head"]])
        self.assertIn(hf["id"], [x["id"] for x in result["new"]])  # base 의 유효 finding 이 없으므로 신규
        self.assertNotIn(hr["id"], [x["id"] for x in result["new"]])
        self.assertEqual(result["persistent"], [])
        self.assertEqual(result["recurred"], [])

    def test_mitigated_presumed_via_head_evidence_no_signal(self):
        base, head = self._run(), self._run()
        asset = self._asset(base)
        bf = self._finding(base["id"], asset, "/mitigated-evidence-path")
        self._checked_evidence(head["id"], asset, f"http://localhost/mitigated-evidence-path{self.PAYLOAD}", "SQLI")
        result = compare.compare_runs(self.conn, base["id"], head["id"])
        self.assertIn(bf["id"], [x["id"] for x in result["mitigated_presumed"]])
        self.assertNotIn(bf["id"], [x["id"] for x in result["unconfirmed"]])

    def test_same_path_other_class_or_param_is_not_mitigation(self):
        """H2 재현: base 의 /item q SQLi 가 head 에서 같은 경로의 다른 분류(XSS)·다른 매개변수 요청으로
        '조치 추정'이 되면 안 된다 — 그 지점은 다시 시험되지 않았다."""
        base, head = self._run(), self._run()
        asset = self._asset(base)
        bf = self._finding(base["id"], asset, "/item")
        self._checked_evidence(head["id"], asset, "http://localhost/item?q=<script>alert(1)</script>", "XSS")
        self._checked_evidence(head["id"], asset, "http://localhost/item?other=' OR '1'='1", "SQLI")
        result = compare.compare_runs(self.conn, base["id"], head["id"])
        self.assertIn(bf["id"], [x["id"] for x in result["unconfirmed"]])
        self.assertNotIn(bf["id"], [x["id"] for x in result["mitigated_presumed"]])

    def test_evidence_without_signal_check_is_not_mitigation(self):
        """같은 지점에 요청은 갔지만 신호 판정 기록이 없으면(판정 안 됨) 미확인."""
        base, head = self._run(), self._run()
        asset = self._asset(base)
        bf = self._finding(base["id"], asset, "/sent-but-not-judged")
        self._evidence_at(head["id"], asset, "/sent-but-not-judged")
        result = compare.compare_runs(self.conn, base["id"], head["id"])
        self.assertIn(bf["id"], [x["id"] for x in result["unconfirmed"]])

    def test_head_rejected_same_point_is_not_mitigation(self):
        """head 에서 같은 지점이 다시 잡혔는데 사람이 기각했다면, 고쳐졌다는 증거가 아니다."""
        base, head = self._run(), self._run()
        asset = self._asset(base)
        bf = self._finding(base["id"], asset, "/head-rejected")
        self._finding(head["id"], asset, "/head-rejected", status="REJECTED")
        self._checked_evidence(head["id"], asset, f"http://localhost/head-rejected{self.PAYLOAD}", "SQLI")
        result = compare.compare_runs(self.conn, base["id"], head["id"])
        self.assertIn(bf["id"], [x["id"] for x in result["unconfirmed"]])

    def test_v1_rows_are_incomparable(self):
        base, head = self._run(), self._run()
        asset = self._asset(base)
        bf = self._finding(base["id"], asset, "/old-rule")
        self.conn.execute("UPDATE findings SET fingerprint_v=1 WHERE id=%s", (bf["id"],))
        self._finding(head["id"], asset, "/old-rule")
        result = compare.compare_runs(self.conn, base["id"], head["id"])
        self.assertIn(bf["id"], [x["id"] for x in result["incomparable"]])
        self.assertEqual(result["persistent"], [])

    def test_mitigated_presumed_via_fixed_retest_without_head_evidence(self):
        base, head = self._run(), self._run()
        asset = self._asset(base)
        bf = self._finding(base["id"], asset, "/mitigated-retest-path")
        self._mark_fixed_retest(bf["id"])
        result = compare.compare_runs(self.conn, base["id"], head["id"])  # head 에 증거 전혀 없음
        self.assertIn(bf["id"], [x["id"] for x in result["mitigated_presumed"]])

    def test_unconfirmed_when_head_never_tested_locator(self):
        base, head = self._run(), self._run()
        asset = self._asset(base)
        bf = self._finding(base["id"], asset, "/untouched-path")
        result = compare.compare_runs(self.conn, base["id"], head["id"])  # head 증거 없음, FIXED 이력 없음
        self.assertIn(bf["id"], [x["id"] for x in result["unconfirmed"]])
        self.assertNotIn(bf["id"], [x["id"] for x in result["mitigated_presumed"]])

    def test_null_fingerprint_goes_to_incomparable(self):
        base, head = self._run(), self._run()
        asset = self._asset(base)
        # locate() 가 위치를 못 뽑는 인자 — fingerprint/locator_hash 둘 다 None(fingerprint.py 의 "모르면 합치지 않는다")
        other = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification) "
            "VALUES (%s,%s,'위치 없는 과거 행','근거','대응','RULE_CONFIRMED') RETURNING id",
            (base["id"], asset)).fetchone()
        result = compare.compare_runs(self.conn, base["id"], head["id"])
        self.assertIn(other["id"], [x["id"] for x in result["incomparable"]])
        all_classified = (result["new"] + [p["base"] for p in result["persistent"]] +
                          [p["base"] for p in result["recurred"]] + result["mitigated_presumed"] +
                          result["unconfirmed"])
        self.assertNotIn(other["id"], [x["id"] for x in all_classified])

    def _bare_run_with_company(self, company):
        """runs.create_run 은 baseline-demo 대상의 공유 자산(company='')을 쓰므로, company 가 다른 두
        실행을 만들려면 직접 끼워 넣는다(test_blocked_streak.py 와 같은 관용구)."""
        run = self.conn.execute(
            "INSERT INTO runs (engagement_id, operator, scope, budget) VALUES "
            "('TEST-COMPARE-COMPANY', 'pytest-compare', %s, %s) RETURNING *", (Jsonb({}), Jsonb({}))).fetchone()
        asset = self.conn.execute(
            "INSERT INTO assets (kind, value, company, source) VALUES "
            "('host', %s, %s, 'operator') ON CONFLICT (company, kind, value) DO UPDATE SET value=EXCLUDED.value "
            "RETURNING id", (f"compare-company-host-{company}", company)).fetchone()["id"]
        self.conn.execute("INSERT INTO run_assets (run_id, asset_id, target_id) VALUES (%s,%s,'t')",
                          (run["id"], asset))
        self.addCleanup(self.conn.commit)
        return run

    def test_company_mismatch_is_rejected(self):
        from fastapi.testclient import TestClient
        from webapi import deps, main
        main.app.dependency_overrides[deps.current_user] = lambda: "pytest-compare"
        client = TestClient(main.app)
        try:
            base = self._bare_run_with_company("company-a")
            head = self._bare_run_with_company("company-b")
            self.conn.commit()
            res = client.get(f"/api/compare?base={base['id']}&head={head['id']}")
            self.assertEqual(res.status_code, 422)
        finally:
            main.app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
