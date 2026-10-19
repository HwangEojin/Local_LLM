"""설정: PostgreSQL DSN 은 환경 변수(.env 포함)에서만, 나머지는 기존 audit 설정 + 선택적 workbench.json.

기존 config/settings.json, scope.json, mcp_servers.json 은 audit.config 로 그대로 읽는다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from audit import config as audit_config
from audit import schemas
from audit.models import ConfigError

DSN_ENV = "WORKBENCH_PG_DSN"
_POS = {"type": "integer", "minimum": 1}
BUDGET = {"type": "object", "additionalProperties": False,
          "properties": {"max_tasks": _POS, "max_tool_calls": _POS, "timeout_s": _POS,
                         "max_explorations": {"type": "integer", "minimum": 0},
                         # 체크리스트 도구 호출(max_tool_calls)과 완전히 분리된 탐색 전용 호출 예산.
                         # 탐색이 이 pool 을 다 써도 체크리스트 쪽 호출은 그대로 남는다(CLAUDE.md §2).
                         "max_tool_calls_exploration": _POS}}
WORKBENCH = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "exclude": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "lease_s": _POS, "max_attempts": _POS, "approval_ttl_h": _POS,
        "min_call_interval_ms": {"type": "integer", "minimum": 0},
        "nmap_path": {"type": "string", "minLength": 1},
        "testssl_path": {"type": "string", "minLength": 1},
        "budget": BUDGET,
    },
}
DEFAULT_BUDGET = {"max_tasks": 2000, "max_tool_calls": 500, "timeout_s": 3600, "max_explorations": 3,
                  "max_tool_calls_exploration": 50}


@dataclass
class Config:
    home: Path
    settings: audit_config.Settings
    dsn: str
    exclude: list[str]
    lease_s: int
    max_attempts: int
    approval_ttl_h: int
    budget: dict
    min_call_interval_ms: int = 0  # 도구 호출 사이 최소 간격(요청 속도 제한)
    nmap_path: str | None = None    # 없으면 PATH 와 기본 설치 경로에서 찾는다
    testssl_path: str | None = None  # testssl.sh 스크립트 경로(없으면 재협상 점검 도구를 등록하지 않음)

    @property
    def stop_file(self) -> Path:  # 전체 중지 스위치: 이 파일이 있으면 모든 도구 호출을 거부한다
        return self.home / "data" / "STOP"

    @property
    def migrations_dir(self) -> Path:
        return Path(__file__).resolve().parents[2] / "migrations"  # 저장소 루트(--home 과 무관)

    @property
    def evidence_dir(self) -> Path:
        return self.settings.path("evidence_dir")


def _load_dotenv(path: Path):
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def load(home: str | None) -> Config:
    root = audit_config.resolve_home(home)
    _load_dotenv(root / ".env")
    dsn = os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        raise ConfigError(f"환경 변수 {DSN_ENV} 가 없음. .env 또는 환경 변수에 PostgreSQL 연결 문자열을 설정하세요 "
                          "(.env.example 참고)")
    wb_path = root / "config" / "workbench.json"
    wb = audit_config.load_json(wb_path, WORKBENCH) if wb_path.exists() else {}
    settings = audit_config.load_settings(root)
    budget = {**DEFAULT_BUDGET, "max_tool_calls": settings.limits["max_tool_calls_per_run"],
              "timeout_s": settings.limits["run_timeout_s"], **wb.get("budget", {})}
    return Config(root, settings, dsn, wb.get("exclude", []), wb.get("lease_s", 300),
                  wb.get("max_attempts", 2), wb.get("approval_ttl_h", 24), budget,
                  wb.get("min_call_interval_ms", 0), wb.get("nmap_path"), wb.get("testssl_path"))


def validate_budget(budget: dict) -> dict:
    errs = schemas.validate(budget, BUDGET)
    if errs:
        raise ConfigError("예산 값이 잘못됨: " + "; ".join(errs))
    return budget
