"""Burp Suite MCP 결과 후처리: 진단 범위 밖 트래픽을 증거에서 제거한다.

Burp 프록시 이력/조직자/스캐너 이슈에는 작업과 무관한 다른 사이트의 트래픽이 섞일 수 있다.
각 항목의 *요청 호스트*가 대상의 범위 안일 때만 남기고, 범위 밖이거나 호스트를 알 수 없는 항목은
저장하지 않는다(fail-closed). 응답 본문에 다른 사이트 주소가 나와도 요청 호스트만 본다.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from ..models import ToolError, ToolOutput

_HOST_HEADER = re.compile(r"(?i)(?:^|\\r\\n|\r?\n)host:[ \t]*([A-Za-z0-9._\-\[\]:]+)")
_HOST_KEYS = ("host", "hostname", "targetHostname")
_URL_KEYS = ("baseUrl", "url", "origin", "target")
_NESTED_KEYS = ("requestResponses", "requests", "request_responses", "evidence")
# "HttpRequestResponse{httpRequest=GET /x HTTP/1.1\r\n, ...}" 형태(실제 Burp MCP 응답, 줄바꿈 없는 JSON
# 문자열 하나로 옴 — test_signals.VerifyCsrf.test_status_line_matches_inside_burp_json_wrapper 에서 실측).
# 요청 줄은 "httpRequest=" 바로 뒤에 와서 앞에 줄바꿈이 없으므로(줄 시작 고정은 안 됨 — 같은 종류의 버그를
# CSRF 쪽에서 이미 실측으로 발견했음), 줄 경계를 요구하지 않고 메서드+경로+HTTP 버전 모양만 찾는다.
_REQUEST_LINE = re.compile(r"\b([A-Z]{3,10})[ \t]+(\S+)[ \t]+HTTP/\d")


def _host_only(h: str) -> str:
    h = h.strip().lower()
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def allowed_hosts(target) -> set[str]:
    if target.type == "host":
        return {_host_only(target.value)}
    if target.type == "url":
        h = urlsplit(target.value).hostname
        return {h.lower()} if h else set()
    return set()  # path 대상에는 네트워크 트래픽 증거를 연결하지 않는다


def split_items(text: str) -> list:
    """JSON 배열, JSON 객체, 연속된 JSON 객체, 빈 줄로 구분된 덩어리를 항목 목록으로 나눈다."""
    text = text.strip()
    if not text or text.lower().startswith("reached end"):  # Burp MCP 가 더 이상 항목이 없을 때 돌려주는 문구
        return []
    try:
        v = json.loads(text)
        return v if isinstance(v, list) else [v]
    except ValueError:
        pass
    items, dec, i = [], json.JSONDecoder(), 0
    while i < len(text):
        while i < len(text) and text[i] in " \t\r\n,":
            i += 1
        if i >= len(text):
            break
        try:
            obj, i = dec.raw_decode(text, i)
            items.append(obj)
        except ValueError:
            items = []
            break
    return items or [c for c in re.split(r"\n\s*\n", text) if c.strip()]


def request_hosts(item) -> set[str]:
    """항목이 가리키는 요청의 호스트 집합. 알 수 없으면 빈 집합."""
    hosts: set[str] = set()
    if isinstance(item, str):
        m = _HOST_HEADER.search(item)
        return {_host_only(m.group(1))} if m else hosts
    if isinstance(item, list):
        for x in item:
            hosts |= request_hosts(x)
        return hosts
    if not isinstance(item, dict):
        return hosts
    for k in _HOST_KEYS:
        if isinstance(item.get(k), str) and item[k].strip():
            hosts.add(_host_only(item[k]))
    for k in _URL_KEYS:
        v = item.get(k)
        if isinstance(v, str) and "://" in v and urlsplit(v).hostname:
            hosts.add(urlsplit(v).hostname.lower())
    if isinstance(item.get("request"), str):
        hosts |= request_hosts(item["request"])
    for k in _NESTED_KEYS:
        if isinstance(item.get(k), (list, dict)):
            hosts |= request_hosts(item[k])
    return hosts


def request_urls(item) -> set[str]:
    """항목이 가리키는 요청의 전체 URL 집합(README §7 "Burp 이력 기반 자산 추출", 2026-10-19 구현).

    프록시 이력은 요청 줄(GET /path HTTP/1.1)과 Host 헤더만 평문으로 보여주고 TLS 여부는 구조적으로
    알려주지 않는 경우가 많아, 스킴을 모르면 http 로 둔다(범위 검사는 호스트 기준이라 스킴을 잘못 추정해도
    범위를 벗어나거나 들어오는 일은 없다). 알 수 없으면 빈 집합.
    """
    urls: set[str] = set()
    if isinstance(item, str):
        line, host = _REQUEST_LINE.search(item), _HOST_HEADER.search(item)
        if line and host:
            path = line.group(2)
            # 자산 URL은 포트를 보존해야 한다(_host_only 는 request_hosts 용으로 포트를 버림 — 여기선 안 됨)
            urls.add(path if path.startswith(("http://", "https://")) else f"http://{host.group(1).strip()}{path}")
        return urls
    if isinstance(item, list):
        for x in item:
            urls |= request_urls(x)
        return urls
    if not isinstance(item, dict):
        return urls
    for k in _URL_KEYS:
        v = item.get(k)
        if isinstance(v, str) and "://" in v:
            urls.add(v)
    if isinstance(item.get("request"), str):
        urls |= request_urls(item["request"])
    for k in _NESTED_KEYS:
        if isinstance(item.get(k), (list, dict)):
            urls |= request_urls(item[k])
    return urls


def scope_filter(output: ToolOutput, target) -> ToolOutput:
    """범위 안 항목만 남긴 새 출력을 돌려준다. 필터를 적용할 수 없으면 ToolError(저장하지 않음)."""
    if output.truncated:
        raise ToolError("출력이 max_evidence_bytes 를 넘어 잘려서 범위 필터를 적용할 수 없음 — count 를 줄이세요")
    try:
        payload = json.loads(output.data)
    except ValueError:
        raise ToolError("MCP 결과를 해석할 수 없어 범위 필터를 적용할 수 없음") from None
    allowed = allowed_hosts(target)
    items = [it for c in payload.get("content", []) if c.get("type") == "text" for it in split_items(c.get("text", ""))]
    kept, out_of_scope, unknown = [], 0, 0
    for it in items:
        hosts = request_hosts(it)
        if not hosts:
            unknown += 1
        elif hosts <= allowed:
            kept.append(it)
        else:
            out_of_scope += 1
    stats = {"allowed_hosts": sorted(allowed), "received": len(items), "kept": len(kept),
             "dropped_out_of_scope": out_of_scope, "dropped_unknown_host": unknown}
    data = json.dumps({"isError": bool(payload.get("isError")), "scope_filter": stats, "items": kept},
                      ensure_ascii=False, indent=1).encode("utf-8")
    return ToolOutput(data, output.tool_version, False, {**output.meta, "scope_filter": stats})
