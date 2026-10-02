"""도구 레지스트리: 여기에 등록된 도구만 실행될 수 있다."""
from __future__ import annotations

from functools import partial

from ..models import ConfigError, ToolSpec
from ..policy import is_loopback_url
from .fs_tools import make_tools


def build_registry(settings, mcp_servers: dict) -> dict[str, ToolSpec]:
    max_bytes = settings.limits["max_evidence_bytes"]
    reg = make_tools(max_bytes)
    for server, srv in mcp_servers.items():
        if not srv["enabled"]:
            continue
        if srv["transport"] == "sse" and not is_loopback_url(srv["url"]) and not settings.external_api_enabled:
            raise ConfigError(f"MCP 서버 '{server}' 의 url이 로컬이 아니며 외부 전송은 비활성 상태임")
        from .. import mcp_client
        for tool, pol in srv["allowed_tools"].items():
            name = f"mcp:{server}/{tool}"
            post = None
            if pol.get("scope_filter") == "burp":
                from .burp import scope_filter
                post = scope_filter
            reg[name] = ToolSpec(name, pol["risk"], pol.get("args_schema", {"type": "object"}),
                                 pol.get("scope_args", {}),
                                 partial(mcp_client.call_tool, srv, tool, max_bytes=max_bytes),
                                 version=f"mcp:{server}", post=post)
    return reg
