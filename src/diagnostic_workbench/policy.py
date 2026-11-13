"""정책 판정 계층(LLM 외부). 모든 도구 호출은 decide() 의 명시적 결과를 받아야 한다.

범위·인자 검사는 기존 audit.policy.Policy 를 그대로 쓰고, 여기서는 제외 대상, 전체 중지 스위치, 인터셉트
규칙(ask), DB 에 기록된 승인(실행·대상·도구·정확한 인자에 묶임)을 더해 네 가지 결과로 정리한다. 판단할 수
없으면 거부한다. 도구 호출 위험도 체계(CLAUDE.md §8, 2026-11 삭제)는 더 이상 승인 게이트가 아니다 — 승인
요구 여부는 인터셉트 규칙 매치 여부로만 정한다.
"""
from __future__ import annotations

import hashlib
import json
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from audit.models import PolicyDenied

ALLOW, ALLOW_WITH_APPROVAL = "ALLOW", "ALLOW_WITH_APPROVAL"
DENY, NEEDS_SCOPE_CONFIRMATION = "DENY", "NEEDS_SCOPE_CONFIRMATION"


@dataclass
class Decision:
    decision: str
    reason: str
    args: dict  # 정책이 정규화한 인자(ALLOW 일 때만 실행에 사용)


def frame_block_message(reason: str) -> str:
    """정책 거부/승인대기 사유를 LLM 에게 보여줄 때만 씌우는 프레이밍(레퍼런스 프로젝트 guard.go 의 systemBlockMessage 참고,
    CLAUDE.md §8, 2026-11 적용). 차단이 '이 워크벤치의 정책 통제'이고 대상(target)의 방어 기전이 아님을
    LLM 에게 명시해, 우회를 시도하지 말고 다른 가설·도구로 전환하도록 유도한다.

    원본 reason 은 감사 로그/DB/운영자 화면에 그대로 남긴다 — 이 함수는 LLM 입력으로 쓰일 문자열에만 적용한다.
    """
    return (f"[워크벤치 정책 통제 — 대상의 방어가 아님] {reason}. 이 작업은 허용되지 않음. "
            "다른 방법으로 우회를 시도하지 말고 다른 가설이나 도구로 전환할 것.")


def args_hash(args: dict) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _excluded(value: str, exclude: list[str]) -> str | None:
    # ponytail: 문자열 접두어/호스트 일치만 본다. CIDR·와일드카드가 필요해지면 ipaddress/fnmatch 로 확장.
    v = value.replace("\\", "/").lower()
    host = (urlsplit(v).hostname or "") if "://" in v else ""
    for ex in exclude:
        e = ex.replace("\\", "/").lower().rstrip("/")
        if v == e or v.startswith(e + "/") or host == e:
            return ex
    return None


def _intercepted(rules: list[dict], tool: str, args: dict) -> str | None:
    """매치한 규칙 이름, 없으면 None. rules 는 config.load_intercept_rules 가 미리 컴파일해 둔 값."""
    if not rules:
        return None
    dumped = None
    for r in rules:
        if not r["tool_re"].search(tool):
            continue
        if r["arg_re"] is None:
            return r["name"]
        dumped = dumped if dumped is not None else json.dumps(args, ensure_ascii=False, default=str)
        if r["arg_re"].search(dumped):
            return r["name"]
    return None


def decide(ctx, tool: str, args, asset_id) -> Decision:
    cfg, target = ctx.cfg, ctx.targets.get(asset_id)
    raw = args if isinstance(args, dict) else {}
    if cfg.stop_file.exists():
        return Decision(DENY, f"전체 중지 스위치가 켜져 있음 ({cfg.stop_file})", raw)
    if target is None:
        return Decision(NEEDS_SCOPE_CONFIRMATION, "대상이 현재 승인된 범위(scope.json)에서 확인되지 않음", raw)
    allowed = ctx.run.get("allowed_tools")
    if allowed is not None and tool not in allowed:
        return Decision(DENY, f"도구 '{tool}' 은(는) 이 실행의 허용 도구 목록에 없음", raw)
    spec = ctx.registry.get(tool)
    try:
        clean = ctx.policy.authorize(spec, tool, args, target)
    except PolicyDenied as e:
        return Decision(DENY, str(e), raw)
    for value in [target.value] + [clean[a] for a in spec.scope_args if a in clean]:
        hit = _excluded(value, cfg.exclude)
        if hit:
            return Decision(DENY, f"제외 대상 '{hit}' 에 해당함: {value}", clean)
        if cfg.exclude and target.type != "path":  # 이름은 범위 안이어도 해석된 주소가 차단 주소일 수 있다
            host = urlsplit(value).hostname if "://" in value else value
            try:
                addrs = {a[4][0] for a in socket.getaddrinfo(host, None)}
            except OSError:
                return Decision(NEEDS_SCOPE_CONFIRMATION, f"'{host}' 의 주소를 해석할 수 없어 제외 대상 여부를 확인하지 못함", clean)
            hit = next((ex for a in sorted(addrs) if (ex := _excluded(a, cfg.exclude))), None)
            if hit:
                return Decision(DENY, f"'{host}' 이(가) 제외 주소 '{hit}' 로 해석됨", clean)
    hit = _intercepted(cfg.intercept_rules, tool, clean)
    if hit is None:  # 인터셉트 규칙 미매치 — 위험도 게이트 없음(CLAUDE.md §8, 2026-11), 범위 내면 바로 허용
        return Decision(ALLOW, "인터셉트 규칙 미매치, 범위 내", clean)
    # 인터셉트 규칙에 걸리면 자율 모드(full 포함)와 무관하게 사람 승인을 강제한다 — 운영자가 '이건 항상
    # 사람이 보게 한다'고 등록한 규칙을 자율 모드로 건너뛸 수 있으면 그 규칙이 통제로서 의미가 없어진다.
    row = ctx.conn.execute(
        "SELECT decision, approver FROM approvals WHERE run_id=%s AND asset_id=%s AND tool=%s AND args_sha256=%s "
        "AND expires_at > now() ORDER BY decided_at DESC LIMIT 1",
        (ctx.run["id"], asset_id, tool, args_hash(clean))).fetchone()
    if row is None:
        return Decision(ALLOW_WITH_APPROVAL, f"가로채기 규칙 '{hit}' 에 걸림 — 사람 승인 필요", clean)
    if row["decision"] != "APPROVED":
        return Decision(DENY, f"승인자 {row['approver']} 이(가) 거부함", clean)
    return Decision(ALLOW, f"승인됨 (승인자 {row['approver']})", clean)


_PAGE_URL = re.compile(r"Page URL: (\S+)")
# @playwright/mcp 의 browser_network_requests 출력 형식(packages/playwright-core/src/tools/backend/network.ts
# renderRequestLine): "N. [METHOD] url => [STATUS] statusText" 또는 "=> [FAILED] errorText".
_NETWORK_REQUEST_LINE = re.compile(r"^\d+\.\s+\[\w+\]\s+(\S+)", re.MULTILINE)


def _texts(data: bytes) -> str:
    """MCP 응답(JSON)의 text 내용. JSON 이 아니면 원문 그대로."""
    raw = data.decode("utf-8", errors="replace")
    try:
        return "\n".join(c.get("text", "") for c in json.loads(raw).get("content", []) if isinstance(c, dict))
    except (json.JSONDecodeError, AttributeError):
        return raw


def _check_landed_url(ctx, target, url: str) -> str | None:
    if url.startswith("about:"):
        return None
    try:
        ctx.policy.check_url(url, target)
    except PolicyDenied as e:
        return f"도구가 범위 밖 주소에 도착함: {e}"
    hit = _excluded(url, ctx.cfg.exclude)
    if hit:
        return f"도구가 제외 대상 '{hit}' 에 도착함: {url}"
    return None


def landed_out_of_scope(ctx, tool: str, data: bytes, asset_id) -> str | None:
    """도구가 실제로 도착한 주소가 범위를 벗어났으면 그 설명을 돌려준다(리다이렉트로 인한 범위 이탈).

    ponytail: 출력 형식을 실제로 확인한 Playwright MCP 만 본다. 다른 도구는 형식 확인 후 추가.
    Page URL(최상위 탐색 결과)은 모든 browser_* 호출 공통 꼬리말에 붙는다. browser_network_requests 는
    그 페이지가 실제로 보낸 하위 요청(XHR/fetch/스크립트/이미지 등)까지 같은 범위 검사를 받는다 — 페이지
    자체는 범위 안이어도 그 안의 하위 요청이 범위 밖으로 나가는 경우를 잡는다(§7 "리다이렉트 하위 요청
    범위 검사", 2026-10-19 구현).
    """
    if not tool.startswith("mcp:playwright/"):
        return None
    target = ctx.targets[asset_id]
    text = _texts(data)
    for url in _PAGE_URL.findall(text):
        err = _check_landed_url(ctx, target, url)
        if err:
            return err
    if tool == "mcp:playwright/browser_network_requests":
        for url in _NETWORK_REQUEST_LINE.findall(text):
            err = _check_landed_url(ctx, target, url)
            if err:
                return f"하위 요청 중 {err}"
    return None
