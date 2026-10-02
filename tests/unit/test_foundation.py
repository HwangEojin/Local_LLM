import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from audit import config, schemas
from audit.models import ConfigError
from audit.storage import Storage

ROOT = Path(__file__).resolve().parents[2]


class SchemaValidatorTest(unittest.TestCase):
    def test_types_required_and_extra(self):
        s = {"type": "object", "required": ["a"], "additionalProperties": False,
             "properties": {"a": {"type": "integer", "minimum": 1}}}
        self.assertEqual(schemas.validate({"a": 2}, s), [])
        self.assertTrue(schemas.validate({}, s))
        self.assertTrue(schemas.validate({"a": 0}, s))
        self.assertTrue(schemas.validate({"a": True}, s))  # bool 은 int 가 아니다
        self.assertTrue(schemas.validate({"a": 1, "b": 1}, s))

    def test_anyof_and_pattern(self):
        s = {"anyOf": [{"type": "string", "pattern": "^x"}, {"type": "null"}]}
        self.assertEqual(schemas.validate(None, s), [])
        self.assertEqual(schemas.validate("xy", s), [])
        self.assertTrue(schemas.validate("y", s))

    def test_unsupported_keyword_fails_closed(self):
        with self.assertRaises(schemas.SchemaError):
            schemas.validate({}, {"$ref": "#/defs/x"})


class ConfigTest(unittest.TestCase):
    def test_shipped_configs_are_valid(self):
        config.load_settings(ROOT)
        config.load_scope(ROOT)
        config.load_mcp_servers(ROOT)

    def test_invalid_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / "config").mkdir()
            with self.assertRaises(ConfigError):
                config.load_scope(home)  # 파일 없음
            (home / "config" / "scope.json").write_text("{not json", encoding="utf-8")
            with self.assertRaises(ConfigError):
                config.load_scope(home)
            (home / "config" / "scope.json").write_text(json.dumps(
                {"engagement_id": "e", "approved_by": "x", "valid_until": "2099-01-01",
                 "allowed_risks": ["read"], "targets": []}), encoding="utf-8")
            with self.assertRaises(ConfigError):  # 빈 대상 목록
                config.load_scope(home)


class StorageTest(unittest.TestCase):
    def test_reopen_preserves_data_and_append_only(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "a.db"
            s = Storage(db)
            s.audit("r1", "evt", {"password": "x"})
            s.close()
            s = Storage(db)  # 재초기화해도 아무것도 삭제되면 안 된다
            rows = s.audit_events("r1")
            self.assertEqual(len(rows), 1)
            self.assertNotIn('"x"', rows[0]["details"])  # 마스킹됨
            with self.assertRaises(sqlite3.DatabaseError):
                s.db.execute("DELETE FROM audit_events")
            s.close()


class LocalOverrideTest(unittest.TestCase):
    def home(self, base, local=None):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        home = Path(d.name)
        (home / "config").mkdir()
        (home / "config" / "mcp_servers.json").write_text(json.dumps({"servers": base}), encoding="utf-8")
        if local is not None:
            (home / "config" / "mcp_servers.local.json").write_text(json.dumps({"servers": local}), encoding="utf-8")
        return home

    @staticmethod
    def srv(command, enabled=False):
        return {"enabled": enabled, "transport": "stdio", "command": command, "timeout_s": 10, "allowed_tools": {}}

    def test_local_file_replaces_whole_server_and_adds_new(self):
        home = self.home({"a": self.srv("placeholder"), "b": self.srv("keep")},
                         {"a": self.srv("C:/real/path", enabled=True), "c": self.srv("new")})
        s = config.load_mcp_servers(home)
        self.assertEqual((s["a"]["command"], s["a"]["enabled"]), ("C:/real/path", True))  # 공용 항목을 통째로 교체
        self.assertEqual(s["b"]["command"], "keep")  # 건드리지 않은 서버는 그대로
        self.assertIn("c", s)  # 새 서버 추가

    def test_without_local_file_uses_shared_config(self):
        self.assertEqual(config.load_mcp_servers(self.home({"a": self.srv("x")}))["a"]["command"], "x")

    def test_invalid_local_file_fails_closed(self):
        with self.assertRaises(ConfigError):
            config.load_mcp_servers(self.home({"a": self.srv("x")}, {"a": {"enabled": True}}))
        bad = self.srv("x")
        bad["transport"] = "sse"  # sse 인데 url 이 없음
        with self.assertRaises(ConfigError):
            config.load_mcp_servers(self.home({"a": self.srv("x")}, {"a": bad}))

    def test_local_config_is_gitignored(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("config/*.local.json", text)

    def test_missing_checklist_manifest_means_no_checklists(self):
        from audit.checklist_engine import Checklists
        with tempfile.TemporaryDirectory() as d:
            cl = Checklists(Path(d))
            self.assertEqual(list(cl.entries()), [])
            with self.assertRaises(ConfigError):
                cl.load("anything", "1")  # 없는 체크리스트는 조용히 넘어가지 않고 명확히 거부


if __name__ == "__main__":
    unittest.main()
