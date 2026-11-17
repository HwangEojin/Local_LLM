"""Finding fingerprint 기반 중복 제거를 실제 PostgreSQL 로 확인한다(UPGRADE_PLAN.md G1).

- 같은 실행에서 같은 지점(자산·분류·메서드·경로·매개변수)이 다시 관찰되면 새 finding 을 만들지 않고 기존
  finding 에 증거만 더한다(occurrence_count 증가, finding_review_history 에 재관찰 1행 추가).
- 매개변수가 다르면 다른 finding(D1 — 경로 원문 비교, ID 치환 안 함과 같은 원칙).
- 다른 실행에서는 같은 fingerprint 라도 서로 다른 행이고 서로 참조하지 않는다(D3b).
- 분류를 모르는(OTHER) finding 끼리는 자동 병합되지 않지만, 같은 위치면 서로 "중복 의심"으로 잡힌다(D2).
- 수동 병합/해제 API, 보고서 대상에서 병합된 finding 제외.
"""
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db, reporting, runs, worker


class FindingDedupDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg = config.load(None)
            cls.conn = db.connect(cls.cfg.dsn)
            db.require_migrated(cls.conn, cls.cfg.migrations_dir)
        except ConfigError as e:
            raise unittest.SkipTest(f"DB 연결/마이그레이션 불가: {e}") from None
        import json
        if "baseline-demo" not in {t["target_id"] for t in json.loads(
                (cls.cfg.home / "config" / "scope.json").read_text(encoding="utf-8"))["targets"]}:
            raise unittest.SkipTest("scope.json 에 baseline-demo 대상이 없음")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def _run_and_asset(self, operator):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], operator, checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        asset_id = self.conn.execute(
            "SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        return run, asset_id

    def _evidence(self, run_id, asset_id, args="{}"):
        return self.conn.execute(
            "INSERT INTO evidence (run_id, asset_id, requirement_id, tool, args, success, rel_path, sha256, size) "
            "VALUES (%s,%s,'TEST-DEDUP','test.manual',%s,true,'test/fake.txt',repeat('a',64),0) RETURNING id",
            (run_id, asset_id, args)).fetchone()["id"]

    def _upsert(self, run_id, asset_id, *, vuln_class, args, evidence_kind, title, evidence_ids):
        def insert(conn):
            return conn.execute(
                "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification) "
                "VALUES (%s,%s,%s,'근거','조치','RULE_CONFIRMED') RETURNING id", (run_id, asset_id, title)
            ).fetchone()["id"]
        return worker.upsert_exploration_finding(self.conn, self.cfg, run_id, asset_id, vuln_class=vuln_class,
                                                  args=args, evidence_kind=evidence_kind, insert_fn=insert,
                                                  evidence_ids=evidence_ids, actor="worker")

    def test_same_signal_twice_merges_into_one_finding(self):
        run, asset_id = self._run_and_asset("pytest-dedup-same")
        args = {"url": "http://x.test/login?u=' OR '1'='1"}
        eid1 = self._evidence(run["id"], asset_id)
        eid2 = self._evidence(run["id"], asset_id)
        fid1, created1 = self._upsert(run["id"], asset_id, vuln_class="SQLI", args=args, evidence_kind="RULE",
                                      title="SQLi 1", evidence_ids=[eid1])
        fid2, created2 = self._upsert(run["id"], asset_id, vuln_class="SQLI", args=args, evidence_kind="RULE",
                                      title="SQLi 2(같은 지점)", evidence_ids=[eid2])
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(fid1, fid2)
        row = self.conn.execute("SELECT occurrence_count FROM findings WHERE id=%s", (fid1,)).fetchone()
        self.assertEqual(row["occurrence_count"], 2)
        n_evidence = self.conn.execute(
            "SELECT count(*) AS n FROM finding_evidence WHERE finding_id=%s", (fid1,)).fetchone()["n"]
        self.assertEqual(n_evidence, 2)
        hist_n = self.conn.execute(
            "SELECT count(*) AS n FROM finding_review_history WHERE finding_id=%s", (fid1,)).fetchone()["n"]
        self.assertEqual(hist_n, 1)  # 최초 생성 이력은 이 헬퍼가 안 남김 — 재관찰 이력 1건만

    def test_different_paths_make_different_findings(self):
        run, asset_id = self._run_and_asset("pytest-dedup-diffpath")
        eid1 = self._evidence(run["id"], asset_id)
        eid2 = self._evidence(run["id"], asset_id)
        fid1, _ = self._upsert(run["id"], asset_id, vuln_class="SQLI",
                               args={"url": "http://x/user/1?q=' OR '1'='1"}, evidence_kind="RULE",
                               title="A", evidence_ids=[eid1])
        fid2, _ = self._upsert(run["id"], asset_id, vuln_class="SQLI",
                               args={"url": "http://x/user/2?q=' OR '1'='1"}, evidence_kind="RULE",
                               title="B", evidence_ids=[eid2])
        self.assertNotEqual(fid1, fid2)  # D1: 경로 원문이 다르므로 다른 finding

    def test_different_params_on_same_path_make_different_findings(self):
        """H1: Burp 원시 요청(content 하나)에서 id 와 name 의 SQLi 는 서로 다른 finding 이어야 한다."""
        run, asset_id = self._run_and_asset("pytest-dedup-diffparam")
        def burp(body):
            return {"content": "POST /item HTTP/1.1\r\nHost: x\r\n"
                               "Content-Type: application/x-www-form-urlencoded\r\n\r\n" + body}
        eid1 = self._evidence(run["id"], asset_id)
        eid2 = self._evidence(run["id"], asset_id)
        fid1, c1 = self._upsert(run["id"], asset_id, vuln_class="SQLI", args=burp("id=1' OR '1'='1"),
                                evidence_kind="RULE", title="id", evidence_ids=[eid1])
        fid2, c2 = self._upsert(run["id"], asset_id, vuln_class="SQLI", args=burp("name=1' OR '1'='1"),
                                evidence_kind="RULE", title="name", evidence_ids=[eid2])
        self.assertTrue(c1 and c2)
        self.assertNotEqual(fid1, fid2)
        v = self.conn.execute("SELECT fingerprint_v FROM findings WHERE id=%s", (fid1,)).fetchone()["fingerprint_v"]
        self.assertEqual(v, 2)

    def test_v1_row_is_never_merged_with_v2(self):
        """옛 규칙(v1) 행은 같은 fingerprint 값이라도 v2 upsert 의 병합 대상이 아니다."""
        from diagnostic_workbench import fingerprint as fpmod
        run, asset_id = self._run_and_asset("pytest-dedup-v1v2")
        args = {"url": "http://x/item?id=1' OR '1'='1"}
        fp, _, _ = fpmod.compute(asset_id, "SQLI", args)
        old = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification, vuln_class, "
            "fingerprint, fingerprint_v) VALUES (%s,%s,'v1','근거','조치','RULE_CONFIRMED','SQLI',%s,1) RETURNING id",
            (run["id"], asset_id, fp)).fetchone()["id"]
        eid = self._evidence(run["id"], asset_id)
        fid, created = self._upsert(run["id"], asset_id, vuln_class="SQLI", args=args, evidence_kind="RULE",
                                    title="v2", evidence_ids=[eid])
        self.assertTrue(created)
        self.assertNotEqual(fid, old)

    def test_refingerprint_classifies_v1_rows(self):
        """HD2: 자동 재계산 없이 목록만 — 증거가 둘 이상 지점이면 split, 하나면 updatable(--apply 로 갱신)."""
        import json as _json
        run, asset_id = self._run_and_asset("pytest-dedup-refp")
        def v1_finding(title, arg_list):
            fid = self.conn.execute(
                "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification, vuln_class, "
                "fingerprint, fingerprint_v) VALUES (%s,%s,%s,'근거','조치','RULE_CONFIRMED','SQLI',md5(%s),1) "
                "RETURNING id", (run["id"], asset_id, title, title)).fetchone()["id"]
            for a in arg_list:
                eid = self._evidence(run["id"], asset_id, _json.dumps(a))
                self.conn.execute("INSERT INTO finding_evidence VALUES (%s,%s)", (fid, eid))
            return str(fid)
        split = v1_finding("v1 합쳐진 것", [{"url": "http://x/a?id=1' OR '1'='1"}, {"url": "http://x/a?name=1' OR '1'='1"}])
        single = v1_finding("v1 하나", [{"url": "http://x/b?id=1' OR '1'='1"}])
        dry = worker.refingerprint_run(self.conn, run["id"], apply=False)
        self.assertEqual([i["finding_id"] for i in dry["split"]], [split])
        self.assertEqual([i["finding_id"] for i in dry["updatable"]], [single])
        v = self.conn.execute("SELECT fingerprint_v FROM findings WHERE id=%s", (single,)).fetchone()["fingerprint_v"]
        self.assertEqual(v, 1)  # dry-run 은 아무것도 안 바꾼다
        worker.refingerprint_run(self.conn, run["id"], apply=True)
        rows = {str(r["id"]): r["fingerprint_v"] for r in self.conn.execute(
            "SELECT id, fingerprint_v FROM findings WHERE id = ANY(%s::uuid[])", ([split, single],)).fetchall()}
        self.assertEqual(rows, {split: 1, single: 2})  # 갈라지는 건 그대로 둔다

    def test_different_runs_do_not_merge_even_with_same_fingerprint(self):
        run_a, asset_a = self._run_and_asset("pytest-dedup-runA")
        run_b, asset_b = self._run_and_asset("pytest-dedup-runB")
        args = {"url": "http://x/shared-endpoint?x=' OR '1'='1"}
        eid_a = self._evidence(run_a["id"], asset_a)
        eid_b = self._evidence(run_b["id"], asset_b)
        fid_a, created_a = self._upsert(run_a["id"], asset_a, vuln_class="SQLI", args=args, evidence_kind="RULE",
                                        title="A", evidence_ids=[eid_a])
        fid_b, created_b = self._upsert(run_b["id"], asset_b, vuln_class="SQLI", args=args, evidence_kind="RULE",
                                        title="B", evidence_ids=[eid_b])
        self.assertTrue(created_a)
        self.assertTrue(created_b)  # D3b: 다른 실행이면 같은 fingerprint 라도 둘 다 새로 만든다
        self.assertNotEqual(fid_a, fid_b)
        fp_a = self.conn.execute("SELECT fingerprint FROM findings WHERE id=%s", (fid_a,)).fetchone()["fingerprint"]
        fp_b = self.conn.execute("SELECT fingerprint FROM findings WHERE id=%s", (fid_b,)).fetchone()["fingerprint"]
        self.assertEqual(fp_a, fp_b)  # fingerprint 는 같다(실행과 무관하게 계산됨) — G6 기준이 될 값

    def test_other_class_same_location_are_suspected_duplicates_not_merged(self):
        run, asset_id = self._run_and_asset("pytest-dedup-other")
        args = {"url": "http://x/weird-endpoint"}
        eid1 = self._evidence(run["id"], asset_id)
        eid2 = self._evidence(run["id"], asset_id)
        fid1, created1 = self._upsert(run["id"], asset_id, vuln_class="OTHER", args=args, evidence_kind="LLM",
                                      title="의심 1", evidence_ids=[eid1])
        fid2, created2 = self._upsert(run["id"], asset_id, vuln_class="OTHER", args=args, evidence_kind="LLM",
                                      title="의심 2", evidence_ids=[eid2])
        self.assertTrue(created1)
        self.assertTrue(created2)  # OTHER 는 자동 병합 안 함(D2)
        self.assertNotEqual(fid1, fid2)
        both = {str(fid1), str(fid2)}
        rows = self.conn.execute(
            "SELECT id, (SELECT count(*) FROM findings d WHERE d.run_id=f.run_id AND d.locator_hash=f.locator_hash "
            "AND d.locator_hash IS NOT NULL AND d.duplicate_of IS NULL AND d.id<>f.id) AS suspected "
            "FROM findings f WHERE f.id = ANY(%s::uuid[])", (list(both),)).fetchall()
        self.assertEqual({str(r["id"]): r["suspected"] for r in rows}, {str(fid1): 1, str(fid2): 1})

    def test_merge_and_unmerge_api(self):
        from fastapi.testclient import TestClient
        from webapi import deps
        from webapi.main import app
        app.dependency_overrides[deps.current_user] = lambda: "pytest-dedup-merge"
        client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

        run, asset_id = self._run_and_asset("pytest-dedup-mergeapi")
        f1 = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification) "
            "VALUES (%s,%s,'원본','근거','조치','RULE_CONFIRMED') RETURNING id", (run["id"], asset_id)).fetchone()
        f2 = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification) "
            "VALUES (%s,%s,'대상','근거','조치','RULE_CONFIRMED') RETURNING id", (run["id"], asset_id)).fetchone()
        eid = self._evidence(run["id"], asset_id)
        self.conn.execute("INSERT INTO finding_evidence VALUES (%s,%s)", (f1["id"], eid))
        self.conn.commit()

        run_code = run["run_code"]
        r = client.post(f"/api/runs/{run_code}/findings/{f1['id']}/merge",
                        json={"into": str(f2["id"]), "reason": "같은 취약점"})
        self.assertEqual(r.status_code, 200, r.text)
        merged = self.conn.execute("SELECT duplicate_of FROM findings WHERE id=%s", (f1["id"],)).fetchone()
        self.assertEqual(str(merged["duplicate_of"]), str(f2["id"]))
        linked = self.conn.execute(
            "SELECT 1 FROM finding_evidence WHERE finding_id=%s AND evidence_id=%s", (f2["id"], eid)).fetchone()
        self.assertIsNotNone(linked)  # 원본 증거가 대상에도 링크됨

        listed = client.get(f"/api/runs/{run_code}/findings").json()
        self.assertNotIn(str(f1["id"]), [row["id"] for row in listed])  # 기본 목록에 안 보임
        listed_all = client.get(f"/api/runs/{run_code}/findings?include_duplicates=true").json()
        self.assertIn(str(f1["id"]), [row["id"] for row in listed_all])

        r2 = client.post(f"/api/runs/{run_code}/findings/{f1['id']}/unmerge", json={"reason": "오판"})
        self.assertEqual(r2.status_code, 200, r2.text)
        unmerged = self.conn.execute("SELECT duplicate_of FROM findings WHERE id=%s", (f1["id"],)).fetchone()
        self.assertIsNone(unmerged["duplicate_of"])

    def test_report_excludes_merged_findings(self):
        run, asset_id = self._run_and_asset("pytest-dedup-report")
        f1 = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification) "
            "VALUES (%s,%s,'병합될 finding','근거','조치','RULE_CONFIRMED') RETURNING id",
            (run["id"], asset_id)).fetchone()
        f2 = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification) "
            "VALUES (%s,%s,'남는 finding','근거','조치','RULE_CONFIRMED') RETURNING id",
            (run["id"], asset_id)).fetchone()
        self.conn.execute("UPDATE findings SET duplicate_of=%s WHERE id=%s", (f2["id"], f1["id"]))
        self.conn.commit()
        meta = {"targets": []}  # 없는 target_id 는 _findings_for_pentest_report 가 기본값으로 채운다
        found, _status_counts = reporting._findings_for_pentest_report(self.conn, self.cfg.evidence_dir, run, meta)
        titles = {f["name"] for f in found}
        self.assertNotIn("병합될 finding", titles)
        self.assertIn("남는 finding", titles)


if __name__ == "__main__":
    unittest.main()
