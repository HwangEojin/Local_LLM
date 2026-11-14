"""G6: 실행 간 비교(compare.compare_runs, /api/compare, UPGRADE_PLAN.md)를 확인한다.

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

    def _finding(self, run_id, asset_id, path, *, status="CONFIRMED", vuln_class="SQLI"):
        fp, loc_hash, locator = fingerprint.compute(asset_id, vuln_class, {"url": f"http://localhost{path}"})
        f = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification, status) "
            "VALUES (%s,%s,%s,'근거','대응','RULE_CONFIRMED',%s) RETURNING *",
            (run_id, asset_id, f"비교 테스트 {path}", status)).fetchone()
        self.conn.execute(
            "UPDATE findings SET vuln_class=%s, fingerprint=%s, locator=%s, locator_hash=%s WHERE id=%s",
            (vuln_class, fp, Jsonb(locator), loc_hash, f["id"]))
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
            (run_id, asset_id, Jsonb({"url": f"http://localhost{path}"}))).fetchone()["id"]

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

    def test_recurred_not_applied_when_base_rejected(self):
        """base 가 REJECTED 면 FIXED 재검증 이력이 있어도 재발이 아니라 지속으로 본다(스펙: "REJECTED 가 아니라")."""
        base, head = self._run(), self._run()
        asset = self._asset(base)
        bf = self._finding(base["id"], asset, "/rejected-but-refound", status="REJECTED")
        self._mark_fixed_retest(bf["id"])
        hf = self._finding(head["id"], asset, "/rejected-but-refound")
        result = compare.compare_runs(self.conn, base["id"], head["id"])
        self.assertIn((bf["id"], hf["id"]), [(p["base"]["id"], p["head"]["id"]) for p in result["persistent"]])
        self.assertEqual(result["recurred"], [])

    def test_mitigated_presumed_via_head_evidence_no_signal(self):
        base, head = self._run(), self._run()
        asset = self._asset(base)
        bf = self._finding(base["id"], asset, "/mitigated-evidence-path")
        self._evidence_at(head["id"], asset, "/mitigated-evidence-path")  # 같은 지점을 때렸으나 finding 없음
        result = compare.compare_runs(self.conn, base["id"], head["id"])
        self.assertIn(bf["id"], [x["id"] for x in result["mitigated_presumed"]])
        self.assertNotIn(bf["id"], [x["id"] for x in result["unconfirmed"]])

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
