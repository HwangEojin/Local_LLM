"""내장 읽기 전용 네트워크 보강 도구(DNS 조회/HTTP HEAD). planner.enrich 가 호출하거나 LLM 이 직접
제안할 수도 있다 — 둘 다 같은 정책 계층(scope_args)과 Worker 파이프라인을 거친다.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from .. import __version__, safe_http
from ..models import ToolError, ToolOutput, ToolSpec

VERSION = f"builtin-net/{__version__}"
_HOST_ARGS = {"type": "object", "additionalProperties": False, "required": ["host"],
              "properties": {"host": {"type": "string", "minLength": 1, "maxLength": 255}}}
_URL_ARGS = {"type": "object", "additionalProperties": False, "required": ["url"],
             "properties": {"url": {"type": "string", "minLength": 1, "maxLength": 2048}}}
_TIMEOUT_S = 8


def make_tools(max_bytes: int) -> dict[str, ToolSpec]:
    def dns_resolve(args):
        try:
            ip = socket.gethostbyname(args["host"])
        except OSError as e:
            raise ToolError(f"DNS 조회 실패: {e}") from None
        return ToolOutput(json.dumps({"host": args["host"], "ip": ip}, ensure_ascii=False).encode(), VERSION)

    def http_head(args):
        # H5·H6: 리다이렉트는 따라가지 않는다 — 정책 검사는 args["url"] 에만 걸렸으므로, 3xx 는 Location 을
        # 결과에 기록만 한다(따라가야 하면 LLM 이 새 작업으로 제안해 정책을 다시 통과한다). OS 의 환경 변수
        # 프록시도 쓰지 않는다.
        req = urllib.request.Request(args["url"], method="HEAD")
        try:
            with safe_http.opener().open(req, timeout=_TIMEOUT_S) as r:
                status, headers = r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            status, headers = e.code, dict(e.headers or {})
        except (urllib.error.URLError, OSError) as e:
            raise ToolError(f"HTTP HEAD 실패: {e}") from None
        result = {"status": status, "headers": headers}
        if 300 <= status < 400:
            location = next((v for k, v in headers.items() if k.lower() == "location"), None)
            if location:
                result["redirect_not_followed"] = urllib.parse.urljoin(args["url"], location)
        body = json.dumps(result, ensure_ascii=False).encode()
        return ToolOutput(body[:max_bytes], VERSION, truncated=len(body) > max_bytes)

    return {
        "net.dns_resolve": ToolSpec("net.dns_resolve", True, _HOST_ARGS, {"host": "host"}, dns_resolve, VERSION),
        "net.http_head": ToolSpec("net.http_head", True, _URL_ARGS, {"url": "url"}, http_head, VERSION),
    }
