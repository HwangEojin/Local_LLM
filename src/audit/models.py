"""공용 데이터 모델."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone


class Verdict(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INCONCLUSIVE = "INCONCLUSIVE"


VERDICT_LABELS_KO = {
    Verdict.PASS: "양호",
    Verdict.FAIL: "취약",
    Verdict.MANUAL_REVIEW: "수동확인",
    Verdict.NOT_APPLICABLE: "해당없음",
    Verdict.INCONCLUSIVE: "진단불가",
}

RISK_LEVELS = ("read", "write", "dangerous")


class AuditError(Exception):
    """기본 오류. CLI는 종료 코드 1로 변환한다."""


class ConfigError(AuditError):
    pass


class PolicyDenied(AuditError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Target:
    target_id: str
    type: str  # path | host | url
    value: str
    platform: str | None = None  # 체크리스트 항목의 platforms 와 대조하는 대상 플랫폼(예: LINUX)


@dataclass
class ToolSpec:
    """등록된(허용 목록) 도구. 등록되지 않은 것은 실행할 수 없다."""
    name: str
    risk: str
    args_schema: dict
    scope_args: dict[str, str]  # 인자 이름 -> "path" | "host" | "url"
    executor: object = None  # 호출 가능 객체: callable(args) -> ToolOutput
    version: str = ""
    post: object = None  # 선택: callable(ToolOutput, target) -> ToolOutput (예: 진단 범위 밖 항목 제거)


@dataclass
class ToolOutput:
    data: bytes
    tool_version: str
    truncated: bool = False
    meta: dict = field(default_factory=dict)


class ToolError(AuditError):
    """등록된 도구가 실행되었으나 실패함(정책 판단이 아님)."""
