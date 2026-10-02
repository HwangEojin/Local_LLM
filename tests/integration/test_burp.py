"""통합: Burp MCP 와 같은 도구 형식의 테스트 서버(실제 MCP stdio 연결)로 증거 수집과 범위 필터를 검증한다."""
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

from audit import config
from audit.adapters import build_registry
from audit.adapters.burp import request_hosts, split_items
from audit.cli import main
from audit.manual_evidence import add_tool_evidence
from audit.models import AuditError, PolicyDenied, ToolError
from audit.policy import Policy
from tests.helpers import PLAN, Home

SERVER = str(Path(__file__).resolve().parents[1] / "fixtures" / "fake_burp_server.py")
PAGING = {"count": {"type": "integer", "minimum": 1, "maximum": 100}, "offset": {"type": "integer", "minimum": 0}}


def schema(extra=None):
    props = {**PAGING, **(extra or {})}
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


def servers(**over):
    return {"burp": {"enabled": True, "transport": "stdio", "command": sys.executable, "args": [SERVER],
                     "timeout_s": 30, "allowed_tools": {
                         "get_proxy_http_history": {"risk": "read", "scope_filter": "burp", "args_schema": schema()},
                         "get_scanner_issues": {"risk": "read", "scope_filter": "burp", "args_schema": schema()},
                         "get_proxy_http_history_regex": {"risk": "read", "scope_filter": "burp", "args_schema": schema(
                             {"regex": {"type": "string", "minLength": 1, "maxLength": 100}})},
                         "send_http1_request": {"risk": "write", "scope_args": {"targetHostname": "host"}}}, **over}}


class BurpTest(unittest.TestCase):
    def setUp(self):
        self.h = Home()
        sc = config.load_scope(self.h.path)
        sc["targets"] = [{"target_id": "srv", "type": "host", "value": "app.test"}]
        self.h.policy = Policy(sc, self.h.path)
        plan = {**PLAN, "targets": ["srv"], "check_ids": ["SAMPLE-004"]}
        self.run_id = self.h.orchestrator().start(self.h.resolve(plan))
        self.reg = build_registry(self.h.settings, servers())

    def tearDown(self):
        self.h.close()

    def add(self, tool, args, reg=None):
        return add_tool_evidence(self.h.storage, self.h.evidence, self.h.policy, reg or self.reg, self.h.settings,
                                 None, self.run_id, "SAMPLE-004", "srv", tool, args)

    def test_history_is_filtered_to_scope_and_linked(self):
        res = self.add("mcp:burp/get_proxy_http_history", {"count": 10, "offset": 0})
        sf = res["scope_filter"]
        self.assertEqual((sf["received"], sf["kept"], sf["dropped_out_of_scope"], sf["dropped_unknown_host"]), (4, 2, 1, 1))
        self.assertEqual(sf["allowed_hosts"], ["app.test"])
        raw = self.h.evidence.load(res["evidence_id"]).decode("utf-8")
        self.assertIn("/search?q=1", raw)
        self.assertIn("cdn.other.test", raw)  # 응답 본문의 외부 주소는 요청 호스트가 아니므로 항목은 유지된다
        self.assertNotIn("OUT-OF-SCOPE-BODY", raw)  # 범위 밖 항목은 저장되지 않는다
        self.assertNotIn("NO-HOST-INFO-BODY", raw)  # 호스트를 알 수 없는 항목도 저장하지 않는다(fail-closed)
        # 판정은 그대로, 증거만 결과에 연결되고 감사 기록이 남는다
        r = self.h.storage.get_result(self.run_id, "SAMPLE-004", "srv")
        self.assertEqual(r["verdict"], "MANUAL_REVIEW")
        self.assertIn(res["evidence_id"], json.loads(r["evidence_ids"]))
        ev = self.h.storage.evidence(evidence_id=res["evidence_id"])
        self.assertEqual((ev["success"], len(ev["sha256"]), ev["tool"]), (1, 64, "mcp:burp/get_proxy_http_history"))
        events = [e["event"] for e in self.h.storage.audit_events(self.run_id)]
        self.assertIn("evidence_added", events)

    def test_scanner_issues_json_array_filtered(self):
        res = self.add("mcp:burp/get_scanner_issues", {"count": 10, "offset": 0})
        raw = self.h.evidence.load(res["evidence_id"]).decode("utf-8")
        self.assertIn("in-scope-issue", raw)
        self.assertNotIn("OUT-OF-SCOPE-ISSUE", raw)
        self.assertEqual(res["scope_filter"]["kept"], 1)

    def test_policy_still_applies(self):
        with self.assertRaises(PolicyDenied):  # 인자 상한(count<=100)
            self.add("mcp:burp/get_proxy_http_history", {"count": 1000, "offset": 0})
        with self.assertRaises(PolicyDenied):  # 추가 인자 거부
            self.add("mcp:burp/get_scanner_issues", {"count": 1, "offset": 0, "x": 1})
        with self.assertRaises(PolicyDenied):  # 쓰기 도구는 scope 의 allowed_risks 에 없어 거부(승인해도 실행 불가)
            self.add("mcp:burp/send_http1_request", {"content": "GET / HTTP/1.1", "targetHostname": "app.test",
                                                     "targetPort": 443, "usesHttps": True})
        with self.assertRaises(PolicyDenied):  # 등록되지 않은 도구
            self.add("mcp:burp/set_proxy_intercept_state", {"intercepting": True})
        with self.assertRaises(AuditError):  # 없는 결과
            add_tool_evidence(self.h.storage, self.h.evidence, self.h.policy, self.reg, self.h.settings, None,
                              self.run_id, "NOPE", "srv", "mcp:burp/get_scanner_issues", {"count": 1, "offset": 0})

    def test_path_target_gets_no_network_evidence(self):
        # 경로 대상에는 허용 호스트가 없으므로 모든 항목이 범위 밖으로 처리되어 아무것도 저장되지 않는다
        from audit.adapters.burp import allowed_hosts
        from audit.models import Target
        self.assertEqual(allowed_hosts(Target("p", "path", "/x")), set())

    def test_truncated_output_fails_closed_and_records_failure(self):
        self.h.settings.raw["limits"]["max_evidence_bytes"] = 300
        reg = build_registry(self.h.settings, servers())
        with self.assertRaises(ToolError):
            self.add("mcp:burp/get_proxy_http_history", {"count": 10, "offset": 0}, reg)
        failed = [e for e in self.h.storage.evidence(self.run_id) if not e["success"]]
        self.assertEqual(len(failed), 1)
        self.assertIn("범위 필터", failed[0]["error"])
        self.assertIsNone(failed[0]["raw_path"])  # 필터를 못 거친 원본은 저장하지 않는다

    def test_parsers_tolerate_output_formats(self):
        a = {"request": "GET / HTTP/1.1\r\nHost: App.Test:8443\r\n\r\n"}
        self.assertEqual(request_hosts(a), {"app.test"})
        self.assertEqual(request_hosts({"baseUrl": "https://app.test/x"}), {"app.test"})
        self.assertEqual(request_hosts({"requestResponses": [{"request": "GET / HTTP/1.1\r\nHost: a.test\r\n"}]}), {"a.test"})
        self.assertEqual(request_hosts("no host here"), set())
        self.assertEqual(len(split_items(json.dumps([a, a]))), 2)  # JSON 배열
        self.assertEqual(len(split_items(json.dumps(a) + "\n\n" + json.dumps(a))), 2)  # 연속된 객체
        self.assertEqual(len(split_items("GET / HTTP/1.1\r\nHost: a.test\r\n\r\n\r\nGET /b HTTP/1.1\r\nHost: b.test")), 2)  # 텍스트

    def test_cli_evidence_add_and_list(self):
        # CLI 는 설정 파일의 MCP 서버를 쓴다: 테스트용 서버로 교체
        (self.h.path / "config" / "mcp_servers.json").write_text(json.dumps({"servers": servers()}), encoding="utf-8")
        sc = config.load_scope(self.h.path)
        sc["targets"] = [{"target_id": "srv", "type": "host", "value": "app.test"}]
        (self.h.path / "config" / "scope.json").write_text(json.dumps(sc), encoding="utf-8")
        self.h.storage.close()

        def cli(*argv):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    rc = main(["--home", str(self.h.path), *argv])
                except SystemExit as e:
                    rc = e.code
            return rc, out.getvalue() + err.getvalue()
        rc, out = cli("evidence", "add", self.run_id, "SAMPLE-004", "srv", "mcp:burp/get_scanner_issues",
                      "--args", '{"count": 10, "offset": 0}')
        self.assertEqual(rc, 0, out)
        self.assertIn("범위 밖 제외 1건", out)
        rc, out = cli("evidence", "list", self.run_id)
        self.assertEqual(rc, 0)
        self.assertIn("mcp:burp/get_scanner_issues", out)
        self.assertEqual(cli("evidence", "add", self.run_id, "SAMPLE-004", "srv", "mcp:burp/get_scanner_issues",
                             "--args", "{bad")[0], 2)
        self.assertEqual(cli("evidence", "add", self.run_id)[0], 2)  # 인자 부족
        self.assertEqual(cli("evidence", "add", self.run_id, "SAMPLE-004", "srv", "mcp:burp/nope")[0], 1)  # 정책 거부

    def test_empty_burp_response_is_not_counted_as_item(self):
        self.assertEqual(split_items("Reached end"), [])  # 실제 Burp MCP 가 빈 이력에 돌려주는 문구
        self.assertEqual(split_items("  reached end of history "), [])


if __name__ == "__main__":
    unittest.main()
