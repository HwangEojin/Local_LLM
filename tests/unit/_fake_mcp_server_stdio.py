"""test_mcp_register.py 가 stdio 등록을 확인할 때 띄우는 가짜 MCP(stdio) 서버."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake-stdio")


@mcp.tool()
def fetch_page(url: str, depth: int = 1) -> str:
    """Fetch a page and return its text."""
    return "ok"


if __name__ == "__main__":
    mcp.run(transport="stdio")
