"""정책 엔진: LLM과 독립적으로 동작한다. 실행 가능한 것을 결정하고 나머지는 모두 거부한다."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from . import schemas
from .models import ConfigError, PolicyDenied, Target, ToolSpec

_SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "[PRIVATE KEY REDACTED]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 ****"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA****"),
    (re.compile(r"(?i)((?:\w*[_-])?(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)"
                r"[\"']?\s*[=:]\s*[\"']?)[^\s\"',;}]+"), r"\1****"),
]


def mask_secrets(text: str) -> str:
    for pat, repl in _SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text


def secret_values(text: str) -> set[str]:
    """증거 원문에서 마스킹 대상 비밀 값(비밀번호, 토큰, 키 등) 자체를 찾는다."""
    values = set()
    for pat, _ in _SECRET_PATTERNS:
        for m in pat.finditer(text):
            whole = m.group(0)
            prefix = m.group(1) if m.lastindex and whole.startswith(m.group(1)) else ""
            val = whole[len(prefix):].strip(" \t\"'=:")
            if len(val) >= 4:
                values.add(val)
    return values


def redact(text: str, values: set[str]) -> str:
    """text 안에 나타나는 알려진 비밀 값을 가린다(LLM 이 근거에 값을 그대로 인용하는 경우 대비)."""
    for v in sorted(values, key=len, reverse=True):
        text = text.replace(v, "****")
    return mask_secrets(text)


def is_loopback_url(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in ("127.0.0.1", "localhost", "::1")


class Policy:
    def __init__(self, scope: dict, home: Path, today: date | None = None):
        self.scope = scope
        self.home = Path(home)
        if (today or date.today()) > date.fromisoformat(scope["valid_until"]):
            raise ConfigError(f"scope 가 {scope['valid_until']} 에 만료됨")
        self.allowed_risks = set(scope["allowed_risks"])
        self.targets: dict[str, Target] = {}
        for t in scope["targets"]:
            if t["target_id"] in self.targets:
                raise ConfigError(f"target_id 중복: {t['target_id']}")
            value = t["value"]
            if t["type"] == "path":
                p = Path(value)
                p = (p if p.is_absolute() else self.home / p).resolve()
                if not p.exists():
                    raise ConfigError(f"scope 의 경로 대상이 존재하지 않음: {p}")
                value = str(p)
            elif t["type"] == "url" and urlsplit(value).scheme not in ("http", "https"):
                raise ConfigError(f"url 대상은 http(s) 여야 함: {value}")
            self.targets[t["target_id"]] = Target(t["target_id"], t["type"], value, t.get("platform"))

    def target(self, target_id: str) -> Target:
        if target_id not in self.targets:
            raise PolicyDenied(f"대상 '{target_id}' 은(는) 승인된 범위에 없음")
        return self.targets[target_id]

    # ---- 범위 검사: 각 함수는 실행기가 사용해야 할 정규화된 값을 반환한다
    def check_path(self, value: str, target: Target) -> str:
        if target.type != "path":
            raise PolicyDenied(f"{target.type} 대상에는 경로 인자를 사용할 수 없음")
        p = Path(value)
        resolved = (p if p.is_absolute() else self.home / p).resolve()  # 심볼릭 링크/정션을 따라가 실제 경로를 구한다
        root = Path(target.value)
        if resolved != root and not resolved.is_relative_to(root):
            raise PolicyDenied(f"경로가 범위를 벗어남: {value} -> {resolved}")
        return str(resolved)

    def check_host(self, value: str, target: Target) -> str:
        allowed = target.value if target.type == "host" else urlsplit(target.value).hostname
        if target.type == "path" or not allowed or value.strip().lower() != allowed.lower():
            raise PolicyDenied(f"호스트 '{value}' 은(는) 대상 {target.target_id} 의 범위 내 호스트가 아님")
        return value.strip()

    def check_url(self, value: str, target: Target) -> str:
        u = urlsplit(value)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise PolicyDenied(f"잘못된 url: {value}")
        if target.type == "host":
            ok = u.hostname.lower() == target.value.lower()
        elif target.type == "url":
            t = urlsplit(target.value)
            ok = (u.scheme, u.netloc.lower()) == (t.scheme, t.netloc.lower()) and \
                u.path.startswith(t.path or "/")
        else:
            ok = False
        if not ok:
            raise PolicyDenied(f"url '{value}' 은(는) 대상 {target.target_id} 의 범위 밖임")
        return value

    # ---- 도구 호출
    def authorize(self, spec: ToolSpec | None, tool_name: str, args: dict, target: Target,
                  approver=None, dry_run: bool = False) -> dict:
        """검증된 인자를 반환하거나 PolicyDenied를 발생시킨다. 승인 여부는 여기서 결정하며 계획이 결정하지 않는다."""
        if spec is None:
            raise PolicyDenied(f"도구 '{tool_name}' 은(는) 등록/허용되지 않음")
        if spec.risk not in self.allowed_risks:
            raise PolicyDenied(f"도구 '{spec.name}' 의 위험도 '{spec.risk}' 는 scope 에서 허용되지 않음")
        if not isinstance(args, dict):
            raise PolicyDenied("도구 인자는 객체여야 함")
        try:
            errs = schemas.validate(args, spec.args_schema)
        except schemas.SchemaError as e:
            raise PolicyDenied(f"도구 '{spec.name}' 의 스키마를 지원하지 않음: {e}") from None
        if errs:
            raise PolicyDenied(f"'{spec.name}' 의 인자가 잘못됨: {'; '.join(errs)}")
        clean = dict(args)
        for arg, kind in spec.scope_args.items():
            if arg in clean:
                if not isinstance(clean[arg], str):
                    raise PolicyDenied(f"범위 검사 인자 '{arg}' 는 문자열이어야 함")
                clean[arg] = getattr(self, f"check_{kind}")(clean[arg], target)
        if spec.risk != "read" and not dry_run:
            if approver is None or approver(spec, clean) is not True:
                raise PolicyDenied(f"'{spec.name}' ({spec.risk}) 은(는) 승인이 필요하나 승인되지 않음")
        return clean


def interactive_approver(spec: ToolSpec, args: dict) -> bool:
    """TTY에서 사람에게 묻는다. 그 외에는 모두 거부한다(fail-closed)."""
    import sys
    if not sys.stdin.isatty():
        return False
    ans = input(f"[승인 요청] {spec.risk.upper()} 도구 '{spec.name}' 인자={mask_secrets(str(args))} "
                f"— 실행을 승인합니까? [y/N] ")
    return ans.strip().lower() == "y"
