"""test_mcp_register.py 가 띄우는 가짜 MCP(SSE) 서버. 실제 MCP 프로토콜로 tools/list 에 답한다."""
import sys

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake", host="127.0.0.1", port=int(sys.argv[1]))


@mcp.tool()
def fetch_page(url: str, depth: int = 1) -> str:
    """Fetch a page and return its text."""
    return "ok"


@mcp.tool()
def read_note(name: str) -> str:
    """Read a note by name."""
    return "note"


if __name__ == "__main__":
    mcp.run(transport="sse")
