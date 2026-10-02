"""SQLite 저장소. 스키마는 IF NOT EXISTS 로 생성하며 기존 데이터는 삭제하지 않는다.

증거, 감사 이벤트, 판정 이력은 추가만 가능하다(트리거로 강제).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import now_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    plan_json TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL,
    checklist_id TEXT NOT NULL,
    checklist_version TEXT NOT NULL,
    checklist_sha256 TEXT NOT NULL,
    engagement_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS results (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    check_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    method TEXT NOT NULL,
    reason TEXT NOT NULL,
    rule_outcome TEXT,
    llm_outcome TEXT,
    evidence_ids TEXT NOT NULL,
    checklist_id TEXT NOT NULL,
    checklist_version TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    PRIMARY KEY (run_id, check_id, target_id)
);
CREATE TABLE IF NOT EXISTS verdict_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL, check_id TEXT NOT NULL, target_id TEXT NOT NULL,
    old_verdict TEXT, new_verdict TEXT NOT NULL,
    reason TEXT NOT NULL, actor TEXT NOT NULL, changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL, check_id TEXT NOT NULL, target_id TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    tool TEXT NOT NULL, tool_version TEXT NOT NULL,
    args_json TEXT NOT NULL,
    raw_path TEXT,
    sha256 TEXT,
    size INTEGER,
    truncated INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT, ts TEXT NOT NULL, event TEXT NOT NULL, details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT, ts TEXT NOT NULL, model TEXT NOT NULL, purpose TEXT NOT NULL,
    prompt_tokens INTEGER, completion_tokens INTEGER,
    token_source TEXT NOT NULL CHECK (token_source IN ('reported', 'estimated', 'unavailable')),
    duration_ms INTEGER, ok INTEGER NOT NULL, error TEXT
);
"""
_IMMUTABLE = ("evidence", "audit_events", "verdict_history")


class Storage:
    def __init__(self, db_path: Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(_SCHEMA)
        for t in _IMMUTABLE:
            for op in ("UPDATE", "DELETE"):
                self.db.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {t}_no_{op.lower()} BEFORE {op} ON {t} "
                    f"BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END")
        self.db.commit()

    def close(self):
        self.db.close()

    # ---- 실행
    def create_run(self, run_id, plan, plan_sha, checklist_id, version, checklist_sha, engagement):
        ts = now_iso()
        self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,NULL)",
                        (run_id, json.dumps(plan, ensure_ascii=False), plan_sha, checklist_id,
                         version, checklist_sha, engagement, "CREATED", ts, ts))
        self.db.commit()

    def set_run_status(self, run_id, status, error=None):
        self.db.execute("UPDATE runs SET status=?, updated_at=?, error=? WHERE run_id=?",
                        (status, now_iso(), error, run_id))
        self.db.commit()

    def get_run(self, run_id):
        return self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()

    # ---- 결과
    def save_result(self, r: dict, actor="engine"):
        prev = self.get_result(r["run_id"], r["check_id"], r["target_id"])
        cols = ("run_id", "check_id", "target_id", "verdict", "method", "reason", "rule_outcome",
                "llm_outcome", "evidence_ids", "checklist_id", "checklist_version",
                "rule_version", "decided_at")
        row = {**r, "decided_at": now_iso()}
        for k in ("rule_outcome", "llm_outcome", "evidence_ids"):
            if not isinstance(row.get(k), (str, type(None))):
                row[k] = json.dumps(row[k], ensure_ascii=False)
        self.db.execute(f"INSERT OR REPLACE INTO results ({','.join(cols)}) VALUES "
                        f"({','.join('?' * len(cols))})", [row.get(c) for c in cols])
        self.db.execute("INSERT INTO verdict_history (run_id, check_id, target_id, old_verdict, "
                        "new_verdict, reason, actor, changed_at) VALUES (?,?,?,?,?,?,?,?)",
                        (r["run_id"], r["check_id"], r["target_id"],
                         prev["verdict"] if prev else None, r["verdict"], r["reason"], actor,
                         row["decided_at"]))
        self.db.commit()

    def add_result_evidence(self, run_id, check_id, target_id, evidence_id):
        """판정은 바꾸지 않고 결과가 참조하는 증거 목록에 증거를 추가한다."""
        r = self.get_result(run_id, check_id, target_id)
        ids = json.loads(r["evidence_ids"]) + [evidence_id]
        self.db.execute("UPDATE results SET evidence_ids=? WHERE run_id=? AND check_id=? AND target_id=?",
                        (json.dumps(ids), run_id, check_id, target_id))
        self.db.commit()

    def get_result(self, run_id, check_id, target_id):
        return self.db.execute("SELECT * FROM results WHERE run_id=? AND check_id=? AND target_id=?",
                               (run_id, check_id, target_id)).fetchone()

    def results(self, run_id):
        return self.db.execute("SELECT * FROM results WHERE run_id=? ORDER BY check_id, target_id",
                               (run_id,)).fetchall()

    def history(self, run_id):
        return self.db.execute("SELECT * FROM verdict_history WHERE run_id=? ORDER BY id",
                               (run_id,)).fetchall()

    # ---- 증거
    def add_evidence(self, rec: dict):
        cols = list(rec)
        self.db.execute(f"INSERT INTO evidence ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [rec[c] for c in cols])
        self.db.commit()

    def evidence(self, run_id=None, evidence_id=None):
        if evidence_id:
            return self.db.execute("SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
        return self.db.execute("SELECT * FROM evidence WHERE run_id=? ORDER BY collected_at, evidence_id",
                               (run_id,)).fetchall()

    # ---- 감사 기록 및 모델 사용량
    def audit(self, run_id, event, details: dict):
        from .policy import mask_secrets  # 지연 import: policy 는 storage 를 import 하지 않는다
        self.db.execute("INSERT INTO audit_events (run_id, ts, event, details) VALUES (?,?,?,?)",
                        (run_id, now_iso(), event,
                         mask_secrets(json.dumps(details, ensure_ascii=False, default=str))))
        self.db.commit()

    def audit_events(self, run_id):
        return self.db.execute("SELECT * FROM audit_events WHERE run_id=? ORDER BY id", (run_id,)).fetchall()

    def record_model_call(self, run_id, model, purpose, prompt_tokens, completion_tokens,
                          token_source, duration_ms, ok, error=None):
        self.db.execute("INSERT INTO model_calls (run_id, ts, model, purpose, prompt_tokens, "
                        "completion_tokens, token_source, duration_ms, ok, error) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (run_id, now_iso(), model, purpose, prompt_tokens, completion_tokens,
                         token_source, duration_ms, int(ok), error))
        self.db.commit()

    def model_calls(self, run_id):
        return self.db.execute("SELECT * FROM model_calls WHERE run_id=? ORDER BY id", (run_id,)).fetchall()

    def count_model_calls(self, run_id) -> int:
        return self.db.execute("SELECT COUNT(*) FROM model_calls WHERE run_id=?", (run_id,)).fetchone()[0]
