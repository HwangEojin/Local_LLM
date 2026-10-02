"""주통 가이드 PDF 쪽 번호 연결 테스트. pdftotext 없이 합성 페이지 텍스트로 검증한다."""
import json
import tempfile
import unittest
from pathlib import Path

from audit import guide_pdf
from audit.importer import write_checklists
from audit.models import ConfigError


def page(code, title, body="본문"):
    return (f"   2026 가이드\n\n   {code}                 UNIX > 3. 서비스 관리\n    (상)"
            f"                 {title}\n 점검 내용   {body}\n")


class GuidePdfTest(unittest.TestCase):
    def setUp(self):
        self.pages = [page("U-58", "SNMP 서비스 구동 점검"), "U-58 계속되는 쪽 (머리말 없음)",
                      page("U-59", "안전한 SNMP 버전 사용"), "U-59 사례 쪽", "U-59 사례 쪽 2",
                      page("U-60", "SNMP Community String 복잡성 설정")]
        self.idx = guide_pdf.index_codes(self.pages)

    def test_index_start_and_end_pages(self):
        self.assertEqual({c: (s, e) for c, (s, e, _) in self.idx.items()},
                         {"U-58": (1, 2), "U-59": (3, 5), "U-60": (6, 6)})

    def test_span_cap_for_unbounded_last_item(self):
        pages = [page("M-04", "마지막 항목")] + ["부록 쪽"] * 30
        s, e, _ = guide_pdf.index_codes(pages)["M-04"]
        self.assertEqual((s, e), (1, 1))  # 부록까지 이어지는 범위는 믿지 않고 시작 쪽만

    def test_parse_refs(self):
        text = "(상) [서비스 관리] U-59 안전한 SNMP 버전 사용\n(중) [서비스 관리] U-60 SNMP Community String 복잡성 설정"
        self.assertEqual(guide_pdf.parse_refs(text), [("U-59", "안전한 SNMP 버전 사용"),
                                                     ("U-60", "SNMP Community String 복잡성 설정")])

    def test_locate_matches_and_flags_title_difference(self):
        loc, warn = guide_pdf.locate("U-59", "안전한 SNMP 버전 사용", self.idx, "g.pdf")
        self.assertEqual(loc, "g.pdf p.3-5 — U-59 안전한 SNMP 버전 사용")
        self.assertIsNone(warn)
        loc, warn = guide_pdf.locate("U-59", "전혀 다른 이름", self.idx, "g.pdf")
        self.assertIsNotNone(loc)  # 코드가 일치하면 연결하되 표기 차이를 경고
        self.assertIn("표기가", warn)
        self.assertEqual(guide_pdf.locate("A-1", "정보보호 정책", self.idx, "g.pdf"), (None, None))  # PDF 밖 코드

    def test_missing_pdftotext_fails_loudly(self):
        orig = guide_pdf.shutil.which
        guide_pdf.shutil.which = lambda _: None
        try:
            with self.assertRaises(ConfigError):
                guide_pdf.extract_pages(Path("x.pdf"))
        finally:
            guide_pdf.shutil.which = orig


class ReplaceDraftTest(unittest.TestCase):
    def data(self, note):
        return {"c-1": {"checklist_id": "c-1", "guide_name": "g", "guide_version": "v", "official": True,
                        "notice": note, "items": []}}

    def test_only_draft_is_replaceable(self):
        with tempfile.TemporaryDirectory() as d:
            cdir = Path(d)
            (cdir / "manifest.json").write_text(json.dumps({"checklists": []}), encoding="utf-8")
            write_checklists(self.data("1"), cdir, "v1", [])
            write_checklists(self.data("2"), cdir, "v1", [], replace_draft=True)
            saved = json.loads((cdir / "versions/c-1/v1.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["notice"], "2")
            man = json.loads((cdir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(man["checklists"][0]["versions"]), 1)  # 중복 항목 없음
            with self.assertRaises(ConfigError):  # replace 없이는 draft 도 덮어쓰지 않음
                write_checklists(self.data("3"), cdir, "v1", [])
            man["checklists"][0]["versions"][0]["status"] = "active"  # 활성화된 것은 항상 보호
            (cdir / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
            with self.assertRaises(ConfigError):
                write_checklists(self.data("4"), cdir, "v1", [], replace_draft=True)
            self.assertEqual(json.loads((cdir / "versions/c-1/v1.json").read_text(encoding="utf-8"))["notice"], "2")


if __name__ == "__main__":
    unittest.main()
