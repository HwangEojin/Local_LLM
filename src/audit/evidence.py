"""증거 저장소: 원본 증거는 한 번만 기록(덮어쓰기 금지)하고 SHA-256 해시를 남긴다."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from .models import ToolOutput, now_iso
from .storage import Storage


class EvidenceIntegrityError(Exception):
    pass


class EvidenceStore:
    def __init__(self, storage: Storage, evidence_dir: Path):
        self.storage = storage
        self.dir = Path(evidence_dir)

    def record(self, run_id, check_id, target, requirement_id, tool, args,
               output: ToolOutput | None = None, error: str | None = None,
               tool_version: str = "") -> str:
        evidence_id = f"EV-{uuid.uuid4().hex[:16]}"
        rec = {"evidence_id": evidence_id, "run_id": run_id, "check_id": check_id,
               "target_id": target.target_id, "requirement_id": requirement_id,
               "collected_at": now_iso(), "target_ref": f"{target.type}:{target.value}",
               "tool": tool, "tool_version": output.tool_version if output else tool_version,
               "args_json": json.dumps(args, ensure_ascii=False, sort_keys=True),
               "raw_path": None, "sha256": None, "size": None, "truncated": 0,
               "success": int(output is not None and error is None), "error": error}
        if output is not None:
            run_dir = self.dir / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            path = run_dir / f"{evidence_id}.bin"
            with open(path, "xb") as f:  # 'x' 모드: 덮어쓰지 않고 실패한다
                f.write(output.data)
            rec.update(raw_path=str(path), sha256=hashlib.sha256(output.data).hexdigest(),
                       size=len(output.data), truncated=int(output.truncated))
        self.storage.add_evidence(rec)
        return evidence_id

    def load(self, evidence_id: str) -> bytes:
        row = self.storage.evidence(evidence_id=evidence_id)
        if row is None or not row["raw_path"]:
            raise EvidenceIntegrityError(f"{evidence_id}: 원본 증거 없음")
        problem = self.verify(row)
        if problem:
            raise EvidenceIntegrityError(problem)
        return Path(row["raw_path"]).read_bytes()

    @staticmethod
    def verify(row) -> str | None:
        """증거 행이 원본 파일과 일치하면 None, 아니면 문제 설명을 반환한다."""
        if not row["success"]:
            return None if row["error"] else f"{row['evidence_id']}: 수집 실패인데 오류 내용이 없음"
        p = Path(row["raw_path"] or "")
        if not p.is_file():
            return f"{row['evidence_id']}: 원본 파일 없음 ({p})"
        if hashlib.sha256(p.read_bytes()).hexdigest() != row["sha256"]:
            return f"{row['evidence_id']}: SHA-256 불일치 (증거가 변조됨)"
        return None
