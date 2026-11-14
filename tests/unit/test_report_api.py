"""웹에서 결과보고서(Word)를 생성/목록/다운로드/승인하는 API(src/webapi/routers/runs.py 끝부분)를 확인한다.

- CLI `workbench report` 와 같은 reporting.generate() 를 그대로 쓴다 — 새 생성 로직 없음.
- 승인은 새 테이블 없이 기존 audit_events 를 재사용(CLAUDE.md §10 초안/승인 구분).
"""
import json
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db, runs as wb_runs, worker


class ReportApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg = config.load(None)
            cls.conn = db.connect(cls.cfg.dsn)
            db.require_migrated(cls.conn, cls.cfg.migrations_dir)
            if "baseline-demo" not in {t["target_id"] for t in json.loads(
                    (cls.cfg.home / "config" / "scope.json").read_text(encoding="utf-8"))["targets"]}:
                raise unittest.SkipTest("scope.json 에 baseline-demo 대상이 없음")
            from fastapi.testclient import TestClient
            from webapi import deps
            from webapi.main import app
        except (ConfigError, ImportError) as e:
            raise unittest.SkipTest(f"DB/웹 의존성 불가: {e}") from None
        cls.deps = deps
        app.dependency_overrides[deps.get_cfg] = lambda: cls.cfg
        app.dependency_overrides[deps.current_user] = lambda: "pytest-report"
        cls.app, cls.client = app, TestClient(app)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "app"):
            cls.app.dependency_overrides.clear()
        if hasattr(cls, "conn"):
            cls.conn.close()

    def _completed_run(self):
        run = wb_runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-report",
                                 checklist_id=None, use_llm=True)
        self.conn.execute("UPDATE runs SET status='COMPLETED' WHERE id=%s", (run["id"],))
        self.conn.commit()
        self.addCleanup(self.conn.commit)
        return run

    def test_generate_list_download_approve_round_trip(self):
        if not any((self.cfg.home / "templates" / "word").glob("웹*결과보고서*.docx")):
            raise unittest.SkipTest("회사 템플릿(templates/word/웹*결과보고서*.docx)이 없음 — "
                                   "test_pentest_report.py 와 같은 전제")
        run = self._completed_run()
        meta = {"customer": "테스트고객사", "project": "pytest 결과보고서",
               "targets": [{"target_id": "baseline-demo", "name": "baseline-demo"}]}

        gen = self.client.post(f"/api/runs/{run['run_code']}/report", json={"meta": meta, "confirm": True})
        self.assertEqual(gen.status_code, 200, gen.text)
        docx = gen.json()["docx"]
        self.assertTrue(docx.endswith(".docx"))

        listed = self.client.get(f"/api/runs/{run['run_code']}/report").json()
        names = [r["file"] for r in listed]
        filename = next(n for n in names if n.endswith(".docx"))
        self.assertIsNone(next(r for r in listed if r["file"] == filename)["approved_by"])

        dl = self.client.get(f"/api/runs/{run['run_code']}/report/{filename}/download")
        self.assertEqual(dl.status_code, 200)
        self.assertGreater(len(dl.content), 0)

        appr = self.client.post(f"/api/runs/{run['run_code']}/report/{filename}/approve", json={"confirm": True})
        self.assertEqual(appr.status_code, 200, appr.text)
        self.assertEqual(appr.json()["approved_by"], "pytest-report")

        listed2 = self.client.get(f"/api/runs/{run['run_code']}/report").json()
        self.assertEqual(next(r for r in listed2 if r["file"] == filename)["approved_by"], "pytest-report")

    def test_approved_report_becomes_stale_after_finding_edit(self):
        """G3 D6: 승인 이후에 그 실행의 finding 이 편집되면 목록 응답에 stale=true 와 건수가 붙는다.
        승인 전 편집은 stale 이 아니다(이 테스트에서 직접 확인하진 않지만 edited_after_approval 계산이
        '승인 시각 이후'만 세는 쿼리이므로 자동으로 보장됨)."""
        if not any((self.cfg.home / "templates" / "word").glob("웹*결과보고서*.docx")):
            raise unittest.SkipTest("회사 템플릿(templates/word/웹*결과보고서*.docx)이 없음 — "
                                   "test_pentest_report.py 와 같은 전제")
        run = self._completed_run()
        asset_id = self.conn.execute(
            "SELECT asset_id FROM run_assets WHERE run_id=%s", (run["id"],)).fetchone()["asset_id"]
        finding = self.conn.execute(
            "INSERT INTO findings (run_id, asset_id, title, rationale, remediation, verification) "
            "VALUES (%s,%s,'stale 테스트','근거','대응','RULE_CONFIRMED') RETURNING id",
            (run["id"], asset_id)).fetchone()
        self.conn.commit()
        meta = {"customer": "테스트고객사", "project": "pytest stale 보고서",
               "targets": [{"target_id": "baseline-demo", "name": "baseline-demo"}]}
        gen = self.client.post(f"/api/runs/{run['run_code']}/report", json={"meta": meta, "confirm": True})
        self.assertEqual(gen.status_code, 200, gen.text)
        filename = next(n for n in (gen.json()["docx"],) if n)
        filename = filename.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]  # 절대경로에서 파일명만

        before_edit = self.client.post(f"/api/runs/{run['run_code']}/report/{filename}/approve", json={"confirm": True})
        self.assertEqual(before_edit.status_code, 200, before_edit.text)
        not_stale = next(r for r in self.client.get(f"/api/runs/{run['run_code']}/report").json()
                         if r["file"] == filename)
        self.assertFalse(not_stale["stale"])

        worker.edit_finding(self.conn, finding["id"], {"title": "수정된 제목"}, "stale 테스트용 수정",
                            actor="reviewer:pytest")
        self.conn.commit()

        after_edit = next(r for r in self.client.get(f"/api/runs/{run['run_code']}/report").json()
                          if r["file"] == filename)
        self.assertTrue(after_edit["stale"])
        self.assertEqual(after_edit["edited_after_approval"], 1)

    def test_report_requires_confirm(self):
        run = self._completed_run()
        r = self.client.post(f"/api/runs/{run['run_code']}/report", json={"meta": {"customer": "x"}})
        self.assertEqual(r.status_code, 400)

    def test_download_rejects_path_traversal(self):
        run = self._completed_run()
        r = self.client.get(f"/api/runs/{run['run_code']}/report/..%2F..%2Fetc%2Fpasswd/download")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
