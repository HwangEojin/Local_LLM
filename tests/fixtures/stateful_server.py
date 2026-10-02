"""상태를 가진 MCP 서버(테스트 전용). Frida 의 session_id, Playwright 의 브라우저 페이지처럼
한 번의 호출이 만든 상태를 다음 호출이 쓰는 서버를 흉내 낸다."""
import os
import time

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stateful-test")
SESSIONS: dict[str, str] = {}


@mcp.tool()
def open_session(target: str) -> str:
    """세션을 만들고 id 를 돌려준다."""
    sid = f"S{len(SESSIONS) + 1}"
    SESSIONS[sid] = target
    return sid


@mcp.tool()
def use_session(session_id: str) -> str:
    """이전 호출이 만든 세션이 있어야 성공한다."""
    if session_id not in SESSIONS:
        raise ValueError(f"unknown session {session_id}")
    return f"ok:{SESSIONS[session_id]}"


@mcp.tool()
def server_pid() -> str:
    return str(os.getpid())


@mcp.tool()
def bridge_down() -> str:
    """브리지 뒤의 프로그램이 꺼져 있을 때: 오류 플래그 없이 본문에 오류 문구를 돌려준다."""
    return "Request failed: HTTPConnectionPool(host='127.0.0.1', port=8091): Max retries exceeded"


@mcp.tool()
def json_error() -> str:
    return '{"error":"Cannot connect to plugin"}'


@mcp.tool()
def slow(seconds: float) -> str:
    time.sleep(seconds)
    return "done"


if __name__ == "__main__":
    mcp.run()
