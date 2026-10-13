"""증적 저장: 원본은 파일(덮어쓰기 금지), DB 에는 상대 경로·SHA-256·크기·메타데이터."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid
from pathlib import Path

from audit.models import ToolOutput

from .db import masked


def store(conn, evidence_dir: Path, *, run_id, asset_id, requirement_id, tool, args, tool_version="",
          rci_id=None, backlog_item_id=None, output: ToolOutput | None = None, error: str | None = None,
          media_type="application/octet-stream", ext="bin", derived_from=None):
    eid = uuid.uuid4()
    rel = sha = size = None
    if output is not None and error is None:
        rel = f"{run_id}/{eid}.{ext}"
        path = Path(evidence_dir) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "xb") as f:  # 'x': 이미 있으면 실패한다
            f.write(output.data)
        sha, size = hashlib.sha256(output.data).hexdigest(), len(output.data)
    conn.execute(
        "INSERT INTO evidence (id, run_id, asset_id, run_checklist_item_id, backlog_item_id, requirement_id, "
        "tool, tool_version, args, rel_path, sha256, size, truncated, success, error, media_type, derived_from, "
        "collected_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())",
        (eid, run_id, asset_id, rci_id, backlog_item_id, requirement_id, tool,
         output.tool_version if output else tool_version, masked(args), rel, sha, size,
         bool(output and output.truncated), rel is not None, error, media_type, derived_from))
    return eid


def verify(row: dict, evidence_dir: Path) -> str | None:
    """DB 행과 원본 파일이 일치하면 None, 아니면 문제 설명."""
    if not row["success"]:
        return None
    p = Path(evidence_dir) / row["rel_path"]
    if not p.is_file():
        return f"{row['id']}: 원본 파일 없음 ({row['rel_path']})"
    data = p.read_bytes()
    if len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
        return f"{row['id']}: 크기 또는 SHA-256 불일치 (증적이 변조됨)"
    return None


_IMAGE_EXT = {"image/png": "png", "image/jpeg": "jpg"}


def mcp_images(data: bytes) -> list[tuple[str, str, bytes]]:
    """MCP 응답(JSON)에 들어 있는 이미지: (미디어 유형, 확장자, 원본 바이트). 해석할 수 없으면 빈 목록."""
    try:
        content = json.loads(data).get("content", [])
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        return []
    out = []
    for c in content:
        if isinstance(c, dict) and c.get("type") == "image" and c.get("mimeType") in _IMAGE_EXT:
            try:
                out.append((c["mimeType"], _IMAGE_EXT[c["mimeType"]], base64.b64decode(c.get("data", ""), validate=True)))
            except (binascii.Error, ValueError):
                continue
    return out
