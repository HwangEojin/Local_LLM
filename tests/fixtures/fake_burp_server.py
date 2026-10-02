"""Burp MCP 서버 흉내(테스트 전용). 실제 Burp 가 아니다: 도구 이름과 인자(count/offset)만 같다.

범위 안(app.test) 항목, 범위 밖(other.test) 항목, 호스트를 알 수 없는 항목을 섞어서 돌려준다.
"""
import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake-burp")


def _req(host, path="/"):
    return f"GET {path} HTTP/1.1\r\nHost: {host}\r\nCookie: session=SECRET-{host}\r\n\r\n"


HISTORY = [
    {"request": _req("app.test", "/search?q=1"),
     "response": "HTTP/1.1 200 OK\r\n\r\n<a href='https://cdn.other.test/x.js'>link</a>", "notes": ""},
    {"request": _req("other.test", "/private"), "response": "HTTP/1.1 200 OK\r\n\r\nOUT-OF-SCOPE-BODY", "notes": ""},
    {"request": _req("app.test", "/login"), "response": "HTTP/1.1 302 Found\r\nLocation: https://sso.other.test/\r\n\r\n",
     "notes": ""},
    {"response": "HTTP/1.1 200 OK\r\n\r\nNO-HOST-INFO-BODY"},
]
ISSUES = [
    {"name": "SQL injection", "severity": "High", "baseUrl": "https://app.test/search", "detail": "in-scope-issue"},
    {"name": "Cookie without HttpOnly", "severity": "Low", "baseUrl": "https://other.test/", "detail": "OUT-OF-SCOPE-ISSUE"},
]


@mcp.tool()
def get_proxy_http_history(count: int, offset: int) -> str:
    """프록시 이력(빈 줄로 구분된 JSON 항목)."""
    return "\n\n".join(json.dumps(x, ensure_ascii=False) for x in HISTORY[offset:offset + count])


@mcp.tool()
def get_scanner_issues(count: int, offset: int) -> str:
    """스캐너 이슈(JSON 배열)."""
    return json.dumps(ISSUES[offset:offset + count], ensure_ascii=False)


@mcp.tool()
def get_proxy_http_history_regex(count: int, offset: int, regex: str) -> str:
    """정규식에 맞는 이력."""
    import re
    hit = [x for x in HISTORY if re.search(regex, json.dumps(x))]
    return "\n\n".join(json.dumps(x, ensure_ascii=False) for x in hit[offset:offset + count])


@mcp.tool()
def send_http1_request(content: str, targetHostname: str, targetPort: int, usesHttps: bool) -> str:
    """요청 전송(쓰기). 테스트에서는 호출되면 안 된다."""
    return "SENT"


if __name__ == "__main__":
    mcp.run()
