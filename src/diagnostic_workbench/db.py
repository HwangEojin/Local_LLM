"""PostgreSQL 연결, SQL 마이그레이션 실행기, 감사·변경 이벤트 기록."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from audit.models import AuditError, ConfigError
from audit.policy import mask_secrets

_LOCK_KEY = 7242001  # 마이그레이션 동시 실행 방지용 advisory lock


def connect(dsn: str) -> psycopg.Connection:
    try:
        return psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10)
    except psycopg.Error as e:
        raise ConfigError(f"PostgreSQL 연결 실패: {str(e).strip()}") from None


def _files(mig_dir: Path) -> list[Path]:
    return sorted(Path(mig_dir).glob("[0-9]*.sql"))


def migration_status(conn, mig_dir: Path) -> tuple[list[str], list[str]]:
    """(적용됨, 미적용) 파일 이름 목록. 적용된 파일이 수정됐으면 오류."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, "
                 "sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
    done = {r["version"]: r["sha256"] for r in conn.execute("SELECT * FROM schema_migrations")}
    conn.commit()
    applied, pending = [], []
    for f in _files(mig_dir):
        sha = hashlib.sha256(f.read_bytes()).hexdigest()
        if f.name not in done:
            pending.append(f.name)
        elif done[f.name] != sha:
            raise AuditError(f"이미 적용된 마이그레이션 {f.name} 의 내용이 바뀜. 새 번호의 파일로 추가하세요")
        else:
            applied.append(f.name)
    return applied, pending


def migrate(conn, mig_dir: Path) -> list[str]:
    _, pending = migration_status(conn, mig_dir)
    conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
    conn.commit()
    try:
        for name in pending:
            f = Path(mig_dir) / name
            with conn.transaction():  # 파일 하나가 통째로 적용되거나 전혀 적용되지 않는다
                if conn.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (name,)).fetchone():
                    continue
                conn.execute(f.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (version, sha256) VALUES (%s, %s)",
                             (name, hashlib.sha256(f.read_bytes()).hexdigest()))
    finally:
        conn.rollback()
        conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
        conn.commit()
    return pending


def require_migrated(conn, mig_dir: Path):
    _, pending = migration_status(conn, mig_dir)
    if pending:
        raise ConfigError(f"미적용 마이그레이션 {pending}. 먼저 실행: python -m diagnostic_workbench migrate")


def masked(obj) -> Jsonb:
    """비밀 값을 가린 JSONB. 마스킹으로 JSON 이 깨지면 문자열 그대로 보존한다."""
    text = mask_secrets(json.dumps(obj, ensure_ascii=False, default=str))
    try:
        return Jsonb(json.loads(text))
    except json.JSONDecodeError:
        return Jsonb({"masked_text": text})


def audit(conn, run_id, event: str, details: dict):
    conn.execute("INSERT INTO audit_events (run_id, event, details) VALUES (%s, %s, %s)",
                 (run_id, event, masked(details)))


def notify_change(conn, run_id, kind: str, ref_id=None):
    """Planner 깨우기 신호. 작업 데이터가 아니므로 유실돼도 큐와 판정 테이블에서 복구된다."""
    conn.execute("INSERT INTO change_events (run_id, kind, ref_id) VALUES (%s, %s, %s)",
                 (run_id, kind, str(ref_id) if ref_id else None))


def resolve_run(conn, ref: str) -> dict:
    rows = conn.execute("SELECT * FROM runs WHERE id::text LIKE %s",
                        (ref.replace("%", "").replace("_", "") + "%",)).fetchall()
    if len(rows) != 1:
        raise AuditError(f"실행 '{ref}' 을(를) 찾을 수 없음" if not rows
                         else f"실행 접두어 '{ref}' 가 여러 건과 일치함")
    return rows[0]
