"""가벼운 단일 사용자 인증. 로컬 단일 운영자 도구를 가정한다(다중 사용자·역할 없음).

자격 증명과 서명 비밀은 환경 변수로만 받는다(.env 포함, 기존 WORKBENCH_PG_DSN 과 같은 방식) — 코드나
설정 파일에 평문 비밀번호를 두지 않는다. 비밀이 없으면 서버 시작을 거부한다(기본 거부).
"""
from __future__ import annotations

import os
import secrets

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from audit.models import ConfigError

COOKIE_NAME = "workbench_session"
SESSION_MAX_AGE_S = 12 * 3600  # 12시간


def _secret() -> str:
    s = os.environ.get("WORKBENCH_WEB_SECRET", "").strip()
    if not s:
        raise ConfigError("환경 변수 WORKBENCH_WEB_SECRET 이 없음 — 세션 서명 비밀을 .env 에 설정하세요")
    return s


def credentials() -> tuple[str, str]:
    user = os.environ.get("WORKBENCH_WEB_USER", "operator").strip()
    password = os.environ.get("WORKBENCH_WEB_PASSWORD", "").strip()
    if not password:
        raise ConfigError("환경 변수 WORKBENCH_WEB_PASSWORD 가 없음 — 웹 UI 비밀번호를 .env 에 설정하세요")
    return user, password


def verify_login(username: str, password: str) -> bool:
    user, expected = credentials()
    # 길이·내용에 따라 비교 시간이 달라지는 타이밍 공격을 피한다(상수 시간 비교).
    return secrets.compare_digest(username, user) and secrets.compare_digest(password, expected)


def make_session_cookie(username: str) -> str:
    return TimestampSigner(_secret()).sign(username).decode("utf-8")


def verify_session_cookie(token: str | None) -> str | None:
    """유효하면 사용자 이름을, 없거나 만료·위조됐으면 None 을 돌려준다."""
    if not token:
        return None
    try:
        return TimestampSigner(_secret()).unsign(token, max_age=SESSION_MAX_AGE_S).decode("utf-8")
    except (BadSignature, SignatureExpired):
        return None
