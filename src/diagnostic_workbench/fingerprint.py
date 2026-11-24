"""Finding 중복 판단에 쓰는 fingerprint/locator 순수 함수(UPGRADE_PLAN.md G1, H1). DB·LLM 의존 없음 — 입력에서
결정론적으로 계산만 한다. METHOD/path/param_names 는 항상 Python 이 증거의 원 인자(args)에서 뽑는다 —
LLM 이 적은 값은 신뢰 경계상 쓰지 않는다(CLAUDE.md §8-5).

v2(H1, 2026-11-17): `params` 는 **실제 HTTP 매개변수 이름**이다. v1 은 페이로드를 담은 도구 인자 키(Burp 의
`content`, Playwright 의 `url`)를 매개변수 이름으로 써서, 같은 경로의 서로 다른 매개변수 취약점이 같은
fingerprint 로 자동 병합됐다. 원시 GET 요청도 인식하지 못했다(CSRF 용 정규식을 빌려 썼기 때문).

- 원시 HTTP 요청(도구 인자 안의 문자열)이면: 요청 줄의 쿼리, 본문(form-urlencoded / JSON 키 경로 / multipart
  name / 그 밖의 본문 전체는 `body`), 경로 세그먼트(`path:<n>`), 헤더(`header:<소문자 이름>`), 쿠키
  (`cookie:<이름>`) 중 **값에 주입 패턴이 걸리는 것**의 이름.
- URL 인자(`url` 류 키)면: 쿼리 매개변수 이름과 경로 세그먼트.
- 그 밖에 이미 이름이 붙은 구조화 인자(`{"params": {"id": ...}}`)는 그 키 이름.
- 값은 퍼센트 인코딩을 한 번 풀어(`unquote_plus`) 원문과 함께 검사한다.

"모르면 합치지 않는다": 위치(메서드·경로)를 못 뽑으면 둘 다 None. 위치는 있지만 매개변수를 특정하지 못하면
fingerprint 는 None(자동 병합 안 함), locator_hash 만 만든다(D2 "중복 의심" 표시용). 예외: CSRF 는 매개변수가
아니라 요청 구조로 판정하는 분류라 메서드·경로만으로 fingerprint 를 만든다.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse

from audit import signals

FINGERPRINT_VERSION = 2

# signals._METHOD_LINE(CSRF 판정용, 상태 변경 메서드만)과 별개다 — 여기서는 GET 등 모든 메서드를 인식한다.
_METHOD_LINE = re.compile(r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(\S+)\s+HTTP/\d", re.M)
_URL_KEYS = ("url", "targetUrl", "target_url", "uri")
_MULTIPART_NAME = re.compile(r'name="([^"]+)"', re.I)
_PARAMLESS_CLASSES = {"CSRF"}  # 매개변수가 아니라 요청 구조(메서드·출처·쿠키)로 판정하는 분류


def strip_query(path: str) -> str:
    """쿼리스트링만 떼어 낸다(D1: 나머지는 원문 그대로 — 퍼센트 인코딩도 안 풀고, 대소문자도 안 바꾸고,
    끝 슬래시도 안 지운다). `/a?x=1` -> `/a`. `?`가 없으면 그대로."""
    return path.split("?", 1)[0]


def _malicious(value: str) -> bool:
    """값(원문 또는 퍼센트 인코딩을 한 번 푼 값)에 알려진 주입 패턴이 있는가."""
    if not isinstance(value, str) or not value:
        return False
    candidates = {value, urllib.parse.unquote_plus(value)}
    return any(p.search(c) for c in candidates for p in signals._INJECTION_PATTERNS.values())


def _query_names(query: str) -> set[str]:
    return {k for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True) if _malicious(v)}


def _path_names(path: str) -> set[str]:
    return {f"path:{i}" for i, seg in enumerate(strip_query(path).split("/")) if seg and _malicious(seg)}


def _json_names(value, prefix: str = "") -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, str) and _malicious(v):
                names.add(key)
            names |= _json_names(v, key)
    elif isinstance(value, list):
        for v in value:  # 배열 원소는 같은 이름으로 묶는다(인덱스는 요청마다 달라질 수 있음)
            if isinstance(v, str) and _malicious(v) and prefix:
                names.add(prefix)
            names |= _json_names(v, prefix)
    return names


def _split_raw(text: str) -> tuple[str, str]:
    """원시 요청을 (헤더부, 본문)으로 나눈다. CRLF 와 LF 둘 다 받는다."""
    for sep in ("\r\n\r\n", "\n\n"):
        if sep in text:
            head, body = text.split(sep, 1)
            return head, body
    return text, ""


def _raw_param_names(text: str, target: str) -> set[str]:
    head, body = _split_raw(text)
    names = _path_names(target)
    if "?" in target:
        names |= _query_names(target.split("?", 1)[1])
    content_type = ""
    for line in head.splitlines()[1:]:
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "content-type":
            content_type = value.lower()
        elif key == "cookie":
            for part in value.split(";"):
                cname, _, cval = part.strip().partition("=")
                if cname and _malicious(cval):
                    names.add(f"cookie:{cname}")
        elif _malicious(value):
            names.add(f"header:{key}")
    body = body.strip()
    if not body:
        return names
    if "application/x-www-form-urlencoded" in content_type:
        names |= _query_names(body)
    elif "json" in content_type or body[:1] in "{[":
        try:
            names |= _json_names(json.loads(body))
        except ValueError:
            if _malicious(body):
                names.add("body")
    elif "multipart/form-data" in content_type:
        # 파트마다 Content-Disposition 의 name= 과 그 파트 내용
        for part in re.split(r"\r?\n--", body):
            m = _MULTIPART_NAME.search(part)
            if m and _malicious(_split_raw(part)[1]):
                names.add(m.group(1))
    elif _malicious(body):
        names.add("body")  # XML 등 이름 없는 본문 — 본문 전체가 주입 지점
    return names


def _find_raw_request(args: dict) -> tuple[str, str, str] | None:
    """도구 인자 안의 문자열 중 원시 HTTP 요청을 찾는다 → (원문, 메서드, 요청 대상)."""
    for value in signals._flatten_strings(args):
        m = _METHOD_LINE.search(value)
        if m:
            return value[m.start():], m.group(1), m.group(2)
    return None


def _url_arg(args: dict) -> str | None:
    for key in _URL_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _structured_names(args, skip: set[str]) -> set[str]:
    """이미 이름이 붙은 구조화 인자에서, 원시 요청·URL 문자열 자체(skip)는 빼고 값이 악성인 키 이름."""
    names: set[str] = set()

    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, str) and v not in skip and _malicious(v):
                    names.add(str(k))
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)

    walk(args)
    return names


def locate(args: dict) -> dict | None:
    """도구 호출 인자에서 {method, path, params} 를 뽑는다. 메서드·경로를 못 얻으면 None."""
    if not isinstance(args, dict):
        return None
    rendered = signals.http_request_text(args)  # http.request(K): 인자 전체가 요청 하나 — 매개변수는 그 요청에서만 뽑는다
    if rendered is not None:
        m = _METHOD_LINE.search(rendered)
        if m is None:
            return None
        target = m.group(2)
        path = strip_query(urllib.parse.urlsplit(target).path or "/") if "://" in target else strip_query(target)
        return {"method": m.group(1), "path": path, "params": sorted(_raw_param_names(rendered, target))}
    raw = _find_raw_request(args)
    url = _url_arg(args)
    skip: set[str] = set()
    if raw is not None:
        text, method, target = raw
        path = strip_query(target)
        if "://" in path:  # 절대 형식 요청 대상(프록시 요청 등)
            path = strip_query(urllib.parse.urlsplit(target).path or "/")
        params = _raw_param_names(text, target)
        skip.add(text)
        skip |= {s for s in signals._flatten_strings(args) if _METHOD_LINE.search(s)}
    elif url is not None:
        method = "GET"
        split = urllib.parse.urlsplit(url) if "://" in url else urllib.parse.urlsplit("//x" + url)
        path = split.path or "/"
        params = _path_names(path) | (_query_names(split.query) if split.query else set())
        skip.add(url)
    else:
        return None
    params |= _structured_names(args, skip)
    return {"method": method, "path": path, "params": sorted(params)}


def compute(asset_id, vuln_class: str | None, args: dict) -> tuple[str | None, str | None, dict]:
    """(fingerprint, locator_hash, locator) 를 돌려준다(v2). 위치를 못 얻으면 전부 None(모르면 합치지
    않는다 — 잘못 합쳐 증거가 묻히는 쪽이 중복보다 나쁘다). 매개변수를 특정하지 못하면 fingerprint 만 None."""
    locator = locate(args)
    if locator is None:
        return None, None, {}
    tail = f"{locator['method']}|{locator['path']}|{','.join(locator['params'])}"
    locator_hash = hashlib.sha256(f"v{FINGERPRINT_VERSION}|{asset_id}|{tail}".encode()).hexdigest()
    fingerprint = None
    if vuln_class and vuln_class in signals.SIGNALS:  # 7개 결정론적 신호 분류일 때만 자동 병합 대상(D2)
        if locator["params"] or vuln_class in _PARAMLESS_CLASSES:
            fingerprint = hashlib.sha256(
                f"v{FINGERPRINT_VERSION}|{asset_id}|{vuln_class}|{tail}".encode()).hexdigest()
    return fingerprint, locator_hash, locator
