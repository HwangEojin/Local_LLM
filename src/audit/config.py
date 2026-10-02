"""설정 로딩. 잘못된 설정은 ConfigError를 발생시킨다(fail-closed)."""
from __future__ import annotations

import json
import os
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
