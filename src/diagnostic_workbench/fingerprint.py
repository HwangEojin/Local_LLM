"""Finding 중복 판단에 쓰는 fingerprint/locator 순수 함수(UPGRADE_PLAN.md G1). DB·LLM 의존 없음 — 입력에서
결정론적으로 계산만 한다. METHOD/path/param_names 는 항상 Python 이 증거의 원 인자(args)에서 뽑는다 —
LLM 이 적은 값은 신뢰 경계상 쓰지 않는다(CLAUDE.md §8-5).
"""
from __future__ import annotations

import hashlib
import re

from audit import signals

_METHOD_LINE = re.compile(r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(\S+)\s+HTTP/\d", re.M)
_URL_KEYS = ("url", "targetUrl", "target_url", "uri")


def strip_query(path: str) -> str:
    """쿼리스트링만 떼어 낸다(D1: 나머지는 원문 그대로 — 퍼센트 인코딩도 안 풀고, 대소문자도 안 바꾸고,
    끝 슬래시도 안 지운다). `/a?x=1` -> `/a`. `?`가 없으면 그대로."""
    return path.split("?", 1)[0]


def _from_raw_request(text: str) -> tuple[str, str] | None:
    m = _METHOD_LINE.search(text)
    return (m.group(1), strip_query(m.group(2))) if m else None


def _from_url_arg(args: dict) -> tuple[str, str] | None:
    for key in _URL_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            # 스킴/호스트가 있으면 떼고 경로만(없으면 그대로 경로로 취급).
            path = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+", "", value.strip()) or "/"
            return "GET", strip_query(path)
    return None


def locate(args: dict) -> dict | None:
    """도구 호출 인자에서 {method, path, params} 를 뽑는다. 둘 다 못 얻으면 None."""
    if not isinstance(args, dict):
        return None
    text = signals._raw_http_request(args)
    found = _from_raw_request(text) if text is not None else None
    if found is None:
        found = _from_url_arg(args)
    if found is None:
        return None
    method, path = found
    params = sorted(signals.injection_params(args))
    return {"method": method, "path": path, "params": params}


def compute(asset_id, vuln_class: str | None, args: dict) -> tuple[str | None, str | None, dict]:
    """(fingerprint, locator_hash, locator) 를 돌려준다. 위치를 못 얻으면 전부 못 만든다(모르면 합치지
    않는다 — 잘못 합쳐 증거가 묻히는 쪽이 중복보다 나쁘다)."""
    locator = locate(args)
    if locator is None:
        return None, None, {}
    base = f"{asset_id}|{locator['method']}|{locator['path']}|{','.join(locator['params'])}"
    locator_hash = hashlib.sha256(base.encode()).hexdigest()
    fingerprint = None
    if vuln_class and vuln_class in signals.SIGNALS:  # 7개 결정론적 신호 분류일 때만 자동 병합 대상(D2)
        fingerprint = hashlib.sha256(f"{asset_id}|{vuln_class}|{locator['method']}|"
                                     f"{locator['path']}|{','.join(locator['params'])}".encode()).hexdigest()
    return fingerprint, locator_hash, locator
