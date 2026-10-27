"""설정 로딩. 잘못된 설정은 ConfigError를 발생시킨다(fail-closed)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import schemas
from .models import ConfigError


def load_json(path: Path, schema: dict | None = None):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"파일을 찾을 수 없음: {path}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path} 의 JSON 형식 오류: {e}") from None
    if schema is not None:
        try:
            errs = schemas.validate(data, schema)
        except schemas.SchemaError as e:
            raise ConfigError(f"{path}: {e}") from None
        if errs:
            raise ConfigError(f"{path} 검증 실패:\n  " + "\n  ".join(errs[:20]))
    return data


@dataclass
class Settings:
    home: Path
    raw: dict

    @property
    def ollama(self) -> dict:
        return self.raw["ollama"]

    @property
    def limits(self) -> dict:
        return self.raw["limits"]

    @property
    def external_api_enabled(self) -> bool:
        return self.raw["external_api"]["enabled"]

    def path(self, key: str) -> Path:
        p = Path(self.raw["paths"][key])
        return p if p.is_absolute() else self.home / p


def resolve_home(home: str | None) -> Path:
    return Path(home or os.environ.get("AUDIT_HOME") or Path.cwd()).resolve()


def load_settings(home: Path) -> Settings:
    return Settings(home, load_json(home / "config" / "settings.json", schemas.SETTINGS))


def load_scope(home: Path) -> dict:
    return load_json(home / "config" / "scope.json", schemas.SCOPE)


def load_llm_profiles(home: Path) -> dict[str, dict]:
    """config/llm_profiles.json(선택) -> {name: profile}. 파일이 없으면 빈 딕셔너리(프로파일 없음)."""
    path = home / "config" / "llm_profiles.json"
    if not path.is_file():
        return {}
    data = load_json(path, schemas.LLM_PROFILES)
    by_name: dict[str, dict] = {}
    for p in data["profiles"]:
        if p["name"] in by_name:
            raise ConfigError(f"{path}: 프로파일 이름 중복: {p['name']}")
        by_name[p["name"]] = p
    return by_name


def load_intercept_rules(home: Path) -> list[dict]:
    """config/intercept_rules.json(공유, 선택) + intercept_rules.local.json(이 PC 전용, 웹 '인터셉트' 화면이 씀,
    Git 제외) -> [{"name", "tool_re", "arg_re"}]. 같은 이름이면 이 PC 전용이 이긴다. enabled=false 인 규칙은
    뺀다(기본 true). 둘 다 없으면 빈 목록(가로채기 없음).

    패턴은 여기서 미리 컴파일해 호출마다 다시 컴파일하지 않는다(policy.decide 가 자주 불림).
    """
    merged: dict[str, dict] = {}
    for name in ("intercept_rules.json", "intercept_rules.local.json"):
        path = home / "config" / name
        if not path.is_file():
            continue
        data = load_json(path, schemas.INTERCEPT_RULES)
        for r in data["rules"]:
            merged[r["name"]] = r
    rules = []
    for r in merged.values():
        if not r.get("enabled", True):
            continue
        try:
            tool_re = re.compile(r["tool_pattern"])
            arg_re = re.compile(r["arg_pattern"]) if r.get("arg_pattern") else None
        except re.error as e:
            raise ConfigError(f"가로채기 규칙 '{r['name']}' 의 정규식이 잘못됨: {e}") from None
        rules.append({"name": r["name"], "tool_re": tool_re, "arg_re": arg_re})
    return rules


def load_mcp_servers(home: Path) -> dict:
    """config/mcp_servers.json(공용 템플릿) + config/mcp_servers.local.json(이 PC 전용, Git 제외)을 합친다.

    local 파일의 서버 항목은 같은 이름의 공용 항목을 통째로 교체하고, 새 이름이면 추가한다.
    """
    data = load_json(home / "config" / "mcp_servers.json", schemas.MCP_SERVERS)
    local_path = home / "config" / "mcp_servers.local.json"
    if local_path.exists():
        data["servers"].update(load_json(local_path, schemas.MCP_SERVERS)["servers"])
    for name, srv in data["servers"].items():
        need = "command" if srv["transport"] == "stdio" else "url"
        if need not in srv:
            raise ConfigError(f"mcp 서버 '{name}': transport {srv['transport']} 에는 '{need}' 가 필요함")
    return data["servers"]
