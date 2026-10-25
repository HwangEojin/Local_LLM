"""FastAPI 의존성: 요청마다 DB 커넥션을 열고 끝에서 닫는다(CLI 와 같은 1회성 커넥션 패턴).

진단 로직은 전부 diagnostic_workbench 모듈을 그대로 부른다 — 여기서는 어댑터 역할만 한다.
"""
from __future__ import annotations

from typing import Generator

from fastapi import Cookie, Depends, HTTPException

from audit.models import AuditError, ConfigError
from diagnostic_workbench import config as wb_config
from diagnostic_workbench import db as wb_db

from . import auth

_CFG = None  # 설정은 변하지 않으므로 프로세스당 한 번만 읽는다(.env, settings.json 등)


def get_cfg() -> wb_config.Config:
    global _CFG
    if _CFG is None:
        try:
            _CFG = wb_config.load(None)
        except ConfigError as e:
            raise HTTPException(500, f"설정 오류: {e}") from None
    return _CFG


def reset_cfg() -> None:
    """관리 API 가 설정 파일을 바꾼 뒤 호출한다. 다음 요청에서 설정을 다시 읽는다."""
    global _CFG
    _CFG = None


def get_conn(cfg: wb_config.Config = Depends(get_cfg)) -> Generator:
    conn = wb_db.connect(cfg.dsn)
    try:
        wb_db.require_migrated(conn, cfg.migrations_dir)
        yield conn
    finally:
        conn.close()


def current_user(session: str | None = Cookie(default=None, alias=auth.COOKIE_NAME)) -> str:
    user = auth.verify_session_cookie(session)
    if user is None:
        raise HTTPException(401, "로그인이 필요함")
    return user


def audit_error_to_http(exc: AuditError) -> HTTPException:
    return HTTPException(400, str(exc))


def load_run(conn, run_ref: str) -> dict:
    """진단 실행을 ref(run_code 또는 id 접두어)로 찾는다. 없으면 404 — runs.py/findings.py 가 공유한다."""
    try:
        return wb_db.resolve_run(conn, run_ref)
    except AuditError as e:
        raise HTTPException(404, str(e)) from None
