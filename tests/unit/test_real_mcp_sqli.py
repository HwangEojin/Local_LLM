"""자동 점검 (B)의 '실제 MCP 도구 연동' 확인: 합성 인자/응답이 아니라 실제 Burp MCP(`send_http1_request`)로
실제 대상(baseline-demo, localhost:9999, Level 1 게시판 검색)에 진짜 불린 기반 SQLi 쌍을 보내고, 그
실제 응답이 worker._promote_exploration_finding 을 거쳐 finding 으로 승격되는지 본다.

Burp 또는 대상이 이 PC 에서 응답하지 않으면 건너뛴다 — 이 테스트는 실제 연결이 전제다. scope.json 의
baseline-demo 로 이미 승인된 자체 테스트 대상에만 쓴다.
"""
import tempfile
import types
import unittest
import urllib.parse
from pathlib import Path

from psycopg.types.json import Jsonb

from audit import config as audit_config, mcp_client, signals
from audit.models import ConfigError, ToolOutput
from diagnostic_workbench import config, db, evidence, worker

_HOST, _PORT = "localhost", 9999
_LOGIN = "/user/login.do"
_SEARCH = "/playground/level1/list.do"


def _reachable(url: str) -> bool:
    import urllib.request
    try:
        urllib.request.urlopen(url, timeout=3)
        return True
    except OSError:
        return False


def _burp_server():
    try:
        srv = audit_config.load_mcp_servers(audit_config.resolve_home(None))["burp"]
    except ConfigError:
        return None
    return srv if srv.get("enabled") else None


def _get_session_cookie(srv) -> str | None:
    """testuser 로 로그인해 세션 쿠키를 얻는다(Level1 은 로그인 후에만 게시판 검색이 보임)."""
    body = "userId=testuser&userPw=test1234"
    content = (f"POST {_LOGIN} HTTP/1.1\r\nHost: {_HOST}\r\n"
              f"Content-Type: application/x-www-form-urlencoded\r\nContent-Length: {len(body)}\r\n\r\n{body}")
    out = mcp_client.call_tool(srv, "send_http1_request", {
        "content": content, "targetHostname": _HOST, "targetPort": _PORT, "usesHttps": False}, 200_000)
    text = out.data.decode("utf-8", errors="replace")
    m = __import__("re").search(r"JSESSIONID=([0-9A-F]+)", text)
    return m.group(1) if m else None


@unittest.skipUnless(_reachable(f"http://{_HOST}:{_PORT}/"), "baseline-demo(localhost:9999)가 응답하지 않음")
class RealMcpSqliPromotion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = _burp_server()
        if cls.srv is None:
            raise unittest.SkipTest("Burp MCP 서버가 설정되지 않았거나 꺼져 있음")
        cls.session = _get_session_cookie(cls.srv)
        if not cls.session:
            raise unittest.SkipTest("테스트 계정 로그인 실패 — baseline-demo 시드 데이터 확인 필요")
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
            "VALUES ('TEST-REAL-MCP-SQLI', 'pytest', %s, %s, %s) RETURNING id",
            (cv["id"], Jsonb({}), Jsonb({}))).fetchone()["id"]
        cls.asset = cls.conn.execute(
            "INSERT INTO assets (kind, value, source) VALUES ('host', 'real-mcp-sqli-test.invalid', "
            "'operator') ON CONFLICT (company, kind, value) DO UPDATE SET value = EXCLUDED.value RETURNING id"
        ).fetchone()["id"]
        cls._tmp = tempfile.TemporaryDirectory()
        cls.evidence_dir = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "_tmp"):
            cls._tmp.cleanup()
        if hasattr(cls, "conn"):
            cls.conn.rollback()
            cls.conn.close()

    def _request(self, keyword: str) -> dict:
        path = f"{_SEARCH}?searchType=title&keyword=" + urllib.parse.quote(keyword)
        content = (f"GET {path} HTTP/1.1\r\nHost: {_HOST}\r\nCookie: JSESSIONID={self.session}\r\n"
                  "Connection: close\r\n\r\n")
        return {"content": content, "targetHostname": _HOST, "targetPort": _PORT, "usesHttps": False}

    def _promote(self, args: dict) -> int:
        out = mcp_client.call_tool(self.srv, "send_http1_request", args, 2_000_000)
        fact = self.conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'fact','worker','{}') "
            "RETURNING id", (self.run_id,)).fetchone()["id"]
        eid = evidence.store(self.conn, self.evidence_dir, run_id=self.run_id, asset_id=self.asset,
                             requirement_id="explore", tool="mcp:burp/send_http1_request", args=args,
                             output=ToolOutput(out.data, "burp/real"))
        ctx = types.SimpleNamespace(conn=self.conn, run={"id": self.run_id},
                                    cfg=types.SimpleNamespace(evidence_dir=self.evidence_dir))
        worker._promote_exploration_finding(ctx, {"asset_id": self.asset}, fact, eid, args, out.data)
        return fact

    def test_real_boolean_sqli_against_live_target_is_promoted(self):
        self._promote(self._request("zzz' OR '1'='2' -- "))  # 거짓 조건 먼저(짝의 기준)
        fact_true = self._promote(self._request("zzz' OR '1'='1' -- "))

        finding = self.conn.execute(
            "SELECT f.id, f.severity FROM exploration_edges e "
            "JOIN exploration_anchors a ON a.node_id = e.dst_id JOIN findings f ON f.id = a.finding_id "
            "WHERE e.src_id = %s AND e.rel = 'proves'", (fact_true,)).fetchone()
        self.assertIsNotNone(finding, "실제 대상의 실제 SQLi 가 finding 으로 승격되지 않음")
        self.assertEqual(finding["severity"], "high")


if __name__ == "__main__":
    unittest.main()
