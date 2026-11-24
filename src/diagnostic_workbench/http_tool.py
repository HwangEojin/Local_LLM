"""자체 HTTP 요청 도구 `http.request` 와 인증 세션(UPGRADE_PLAN.md K).

- 실행은 `audit.safe_http.opener()` 그대로: 3xx 는 따라가지 않고(Location 만 응답에 남음 — 따라가려면 새 호출이
  정책을 다시 받음), OS 환경 변수 프록시를 쓰지 않는다(H5/H6). 범위 검사는 다른 도구처럼 `policy.decide()` 가
  `url` 인자에 한다.
- Host 헤더는 항상 `url` 의 호스트다 — 헤더·raw 인자로 바꿀 수 없다(같은 IP 의 범위 밖 가상호스트로 가지 못하게).
- 증거 본문은 **응답만**(상태 줄·헤더·본문) — 결정론적 신호 판정이 보는 데이터다. 실제로 보낸 요청과 로그인
  교환은 파생 증거(attachments)로 따로 남긴다. 요청에 든 페이로드가 '응답에 반사됐다'로 오판되지 않게 분리한다.
  보낸 요청에는 세션 값이 그대로 들어간다(KD4: 증거 원본은 그대로). LLM 발췌는 known_secrets()·mask_secrets 로 가린다.
- 세션(KD3): `config/auth_sessions.json` 에 이름·대상·방식과 자격 증명이 든 **환경 변수 이름**만 둔다(비밀값 평문
  없음). 세션 값은 이 프로세스 메모리에만 있다(재시작하면 다시 로그인). LLM 에게는 세션 이름만 보인다.
  로그인 URL 과 세션을 쓰는 요청 URL 은 둘 다 그 세션 대상의 범위 안이어야 한다.
"""
from __future__ import annotations

import json
import os
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from audit import config as audit_config, safe_http
from audit.models import ConfigError, PolicyDenied, ToolError, ToolOutput, ToolSpec
from audit.policy import Policy

VERSION = "builtin-http/1"
_TIMEOUT_S = 15
_METHODS = ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
_DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_DROP_HEADERS = {"host", "content-length"}  # Host 는 url 로 고정, 길이는 urllib 가 실제 본문으로 계산
_ENV = {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]*$"}
SESSIONS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["sessions"],
    "properties": {"sessions": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["name", "target_id", "type"],
        "properties": {
            "name": {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,64}$"},
            "target_id": {"type": "string", "minLength": 1},
            "type": {"enum": ["static", "form", "json"]},
            # static: 붙일 헤더 이름 -> 그 값이 든 환경 변수 이름(예: {"Cookie": "WB_USER_A_COOKIE"})
            "headers_from": {"type": "object", "additionalProperties": _ENV},
            # form/json: 로그인 요청. fields 값의 {{username}}/{{password}} 를 환경 변수 값으로 바꾼다
            "login_url": {"type": "string", "pattern": "^https?://"},
            "method": {"enum": ["POST", "PUT"]},
            "fields": {"type": "object", "additionalProperties": {"type": "string"}},
            "username_from": _ENV, "password_from": _ENV,
            "success_status": {"type": "array", "items": {"type": "integer"}},
            # json: 응답 JSON 에서 토큰을 꺼낼 점 경로(예: "data.token")와 붙일 헤더(기본 Authorization: Bearer ...)
            "token_path": {"type": "string", "minLength": 1},
            "token_header": {"type": "string", "minLength": 1},
            "token_prefix": {"type": "string"},
        }}}},
}


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ToolError(f"환경 변수 {name} 이(가) 비어 있음 — .env 에 세션 자격 증명을 넣으세요")
    return value


def _cookie_pair(set_cookie: str) -> tuple[str, str] | None:
    name, sep, value = set_cookie.split(";", 1)[0].partition("=")
    return (name.strip(), value.strip()) if sep and name.strip() else None


class _Session:
    def __init__(self, spec: dict):
        self.spec = spec
        self.cookies: dict[str, str] = {}
        self.headers: dict[str, str] = {}
        self.ready = False


class SessionStore:
    """세션 설정을 읽고, 필요할 때 로그인해 쿠키·토큰을 붙인다. 실행 프로세스 하나의 메모리에만 산다."""

    def __init__(self, home: Path, upstream_proxy: str | None = None):
        self.home = Path(home)
        self.upstream_proxy = upstream_proxy
        self._sessions: dict[str, _Session] | None = None
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self.home / "config" / "auth_sessions.json"

    def _load(self) -> dict[str, _Session]:
        if self._sessions is None:
            doc = audit_config.load_json(self.path, SESSIONS_SCHEMA) if self.path.exists() else {"sessions": []}
            sessions: dict[str, _Session] = {}
            for spec in doc["sessions"]:
                if spec["name"] in sessions:
                    raise ConfigError(f"auth_sessions.json: 세션 이름 중복 '{spec['name']}'")
                if spec["type"] == "static" and not spec.get("headers_from"):
                    raise ConfigError(f"세션 '{spec['name']}': static 은 headers_from 이 필요함")
                if spec["type"] in ("form", "json") and not all(
                        spec.get(k) for k in ("login_url", "fields", "username_from", "password_from")):
                    raise ConfigError(f"세션 '{spec['name']}': {spec['type']} 은 login_url/fields/username_from/"
                                      "password_from 이 필요함")
                sessions[spec["name"]] = _Session(spec)
            self._sessions = sessions
        return self._sessions

    def names(self) -> list[str]:
        return sorted(self._load())

    def known_secrets(self) -> set[str]:
        """지금까지 이 프로세스가 다룬 세션 값·자격 증명 — LLM 에 가는 발췌에서 가릴 값들."""
        return set(self._secrets)

    def _check(self, session: _Session, url: str) -> None:
        policy = Policy(audit_config.load_scope(self.home), self.home)
        try:
            policy.check_url(url, policy.target(session.spec["target_id"]))
        except PolicyDenied as e:
            raise ToolError(f"세션 '{session.spec['name']}' 은(는) 이 URL 에 쓸 수 없음: {e}") from None

    def _login(self, s: _Session, log: list[str]) -> None:
        spec = s.spec
        if spec["type"] == "static":
            for header, env in spec["headers_from"].items():
                value = _env(env)
                self._secrets.add(value)
                if header.lower() == "cookie":
                    for part in value.split(";"):
                        pair = _cookie_pair(part)
                        if pair:
                            s.cookies[pair[0]] = pair[1]
                            self._secrets.add(pair[1])  # 응답이 값만 되돌려줘도 가려지게(이름=값 통째로만 등록하면 놓침)
                else:
                    s.headers[header] = value
            log.append(f"# 세션 '{spec['name']}': static (환경 변수 {', '.join(spec['headers_from'].values())})")
            s.ready = True
            return
        self._check(s, spec["login_url"])
        user, password = _env(spec["username_from"]), _env(spec["password_from"])
        self._secrets |= {password}
        fields = {k: v.replace("{{username}}", user).replace("{{password}}", password) for k, v in spec["fields"].items()}
        if spec["type"] == "json":
            body, ctype = json.dumps(fields).encode(), "application/json"
        else:
            body, ctype = urllib.parse.urlencode(fields).encode(), "application/x-www-form-urlencoded"
        method = spec.get("method", "POST")
        req = urllib.request.Request(spec["login_url"], data=body, method=method,
                                     headers={"Content-Type": ctype, "User-Agent": _DEFAULT_UA})
        status, _, headers, data = _send(req, self.upstream_proxy, None, 1_000_000)
        u = urllib.parse.urlsplit(spec["login_url"])
        log += [f"# 세션 '{spec['name']}' 로그인 ({spec['type']}) — 요청 본문은 자격 증명이라 생략",
                f"{method} {u.path or '/'} HTTP/1.1", f"Host: {u.netloc}", f"Content-Type: {ctype}", "",
                f"HTTP/1.1 {status}", *[f"{k}: {v}" for k, v in headers.items()], ""]
        if status not in spec.get("success_status", [200, 201, 204, 302, 303]):
            raise ToolError(f"세션 '{spec['name']}' 로그인 실패: 상태 {status}")
        self._absorb(s, headers.get_all("Set-Cookie") or [])
        if spec.get("token_path"):
            try:
                token = json.loads(data.decode("utf-8", "replace"))
                for key in spec["token_path"].split("."):
                    token = token[key]
            except (ValueError, KeyError, TypeError, IndexError):
                raise ToolError(f"세션 '{spec['name']}' 로그인 응답에서 토큰 경로 '{spec['token_path']}' 를 찾지 못함") from None
            token = str(token)
            self._secrets.add(token)
            s.headers[spec.get("token_header", "Authorization")] = spec.get("token_prefix", "Bearer ") + token
        if not s.cookies and not s.headers:
            raise ToolError(f"세션 '{spec['name']}' 로그인 응답에 쿠키도 토큰도 없음")
        s.ready = True

    def _absorb(self, s: _Session, set_cookies: list[str]) -> None:
        for value in set_cookies:
            pair = _cookie_pair(value)
            if pair:
                s.cookies[pair[0]] = pair[1]
                self._secrets.add(pair[1])

    def apply(self, name: str, url: str, headers: list[tuple[str, str]]) -> list[str]:
        """세션 쿠키·헤더를 headers 에 붙인다(필요하면 먼저 로그인). 로그인 교환 기록 줄을 돌려준다."""
        with self._lock:
            s = self._load().get(name)
            if s is None:
                raise ToolError(f"등록되지 않은 세션 '{name}' (config/auth_sessions.json)")
            self._check(s, url)
            log: list[str] = []
            if not s.ready:
                self._login(s, log)
            names = {k.lower() for k, _ in headers}
            for k, v in s.headers.items():
                if k.lower() not in names:
                    headers.append((k, v))
            if s.cookies:
                jar = "; ".join(f"{k}={v}" for k, v in s.cookies.items())
                for i, (k, v) in enumerate(headers):
                    if k.lower() == "cookie":
                        headers[i] = (k, f"{v}; {jar}")
                        break
                else:
                    headers.append(("Cookie", jar))
            return log

    def update(self, name: str, set_cookies: list[str]) -> None:
        """서버가 세션 쿠키를 갈아 끼우면(로그인 후 재발급 등) 다음 요청부터 새 값을 쓴다."""
        with self._lock:
            s = self._load().get(name)
            if s is not None:
                self._absorb(s, set_cookies)


def _send(req, upstream_proxy, ssl_context, max_bytes):
    """(상태, 사유 문구, 헤더, 본문). 4xx/5xx 와 따라가지 않은 3xx 도 응답 자체가 증거라 예외가 아니다."""
    try:
        with safe_http.opener(upstream_proxy, ssl_context).open(req, timeout=_TIMEOUT_S) as r:
            return r.status, r.reason or "", r.headers, r.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        return e.code, str(e.reason or ""), e.headers, (e.read(max_bytes + 1) if e.fp else b"")
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLError):
            raise ToolError(f"TLS 오류: {e.reason} — 자체 서명 인증서 대상이면 insecure=true 로 다시 시도") from None
        raise ToolError(f"HTTP 요청 실패: {e.reason}") from None
    except OSError as e:
        raise ToolError(f"HTTP 요청 실패: {e}") from None


def _parse_raw(raw: str) -> tuple[str, str, list[tuple[str, str]], str]:
    text = raw.replace("\r\n", "\n").lstrip("\n")
    head, _, body = text.partition("\n\n")
    lines = head.split("\n")
    parts = lines[0].split()
    if len(parts) < 2:
        raise ToolError("raw: 요청 줄을 읽지 못함(예: 'POST /login HTTP/1.1')")
    headers = []
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep and key.strip():
            headers.append((key.strip(), value.strip()))
    return parts[0].upper(), parts[1], headers, body


_STORES: dict[str, SessionStore] = {}
_STORES_LOCK = threading.Lock()


def store_for(cfg) -> SessionStore:
    """프로세스 하나에 설정 홈마다 세션 저장소 하나 — 도구 실행과 Worker 의 발췌 가리기가 같은 값을 본다."""
    key = str(Path(cfg.home).resolve())
    with _STORES_LOCK:
        if key not in _STORES:
            _STORES[key] = SessionStore(cfg.home, cfg.upstream_proxy)
        return _STORES[key]


def args_schema(session_names: list[str]) -> dict:
    props = {
        "url": {"type": "string", "pattern": "^https?://", "maxLength": 8192},
        "method": {"enum": _METHODS},
        "headers": {"type": "object", "additionalProperties": {"type": "string", "maxLength": 8192}},
        "body": {"type": "string", "maxLength": 500_000},
        "raw": {"type": "string", "maxLength": 500_000},  # Burp 등에서 복사한 원시 요청. 대상 호스트는 url 로 고정
        "insecure": {"type": "boolean"},  # TLS 인증서 검증 끄기(자체 서명 대상)
    }
    if session_names:
        props["session"] = {"enum": session_names}
    return {"type": "object", "additionalProperties": False, "required": ["url"], "properties": props}


def make_tool(cfg) -> ToolSpec:
    max_bytes = cfg.settings.limits["max_evidence_bytes"]
    store = store_for(cfg)

    def http_request(args):
        u = urllib.parse.urlsplit(args["url"])
        if args.get("raw"):
            if any(k in args for k in ("method", "headers", "body")):
                raise ToolError("raw 와 method/headers/body 는 함께 쓸 수 없음")
            method, target, headers, body = _parse_raw(args["raw"])
            if "://" in target:  # 절대 형식 요청 대상 — url 과 같은 호스트여야 한다
                t = urllib.parse.urlsplit(target)
                if t.netloc.lower() != u.netloc.lower():
                    raise ToolError(f"raw 요청의 대상 호스트({t.netloc})가 url 의 호스트({u.netloc})와 다름")
                target = (t.path or "/") + (f"?{t.query}" if t.query else "")
        else:
            method = args.get("method", "GET")
            target = (u.path or "/") + (f"?{u.query}" if u.query else "")
            headers = [(str(k), str(v)) for k, v in (args.get("headers") or {}).items()]
            body = args.get("body", "")
        headers = [(k, v) for k, v in headers if k.lower() not in _DROP_HEADERS]
        if not any(k.lower() == "user-agent" for k, _ in headers):
            headers.append(("User-Agent", _DEFAULT_UA))
        full = f"{u.scheme}://{u.netloc}{target}"
        log = store.apply(args["session"], full, headers) if args.get("session") else []
        data = body.encode("utf-8") if body else (b"" if method in ("POST", "PUT", "PATCH") else None)
        req = urllib.request.Request(full, data=data, method=method)
        for k, v in headers:
            req.add_header(k, v)
        ctx = ssl._create_unverified_context() if args.get("insecure") else None  # noqa: S323 — 운영자/LLM 이 명시할 때만
        status, reason, resp_headers, payload = _send(req, cfg.upstream_proxy, ctx, max_bytes)
        if args.get("session"):
            store.update(args["session"], resp_headers.get_all("Set-Cookie") or [])
        truncated = len(payload) > max_bytes
        head = f"HTTP/1.1 {status} {reason}".rstrip() + "\r\n" + \
            "".join(f"{k}: {v}\r\n" for k, v in resp_headers.items()) + "\r\n"
        sent = "\r\n".join([f"{method} {target} HTTP/1.1", f"Host: {u.netloc}",
                            *[f"{k}: {v}" for k, v in headers]]) + "\r\n\r\n" + (body or "")
        transcript = ("\r\n".join(log) + "\r\n\r\n" if log else "") + sent
        return ToolOutput(head.encode("utf-8", "replace") + payload[:max_bytes], VERSION, truncated=truncated,
                          attachments=[("request", "message/http", "txt", transcript.encode("utf-8", "replace"))])

    return ToolSpec("http.request", False, args_schema(store.names()), {"url": "url"}, http_request, VERSION)
