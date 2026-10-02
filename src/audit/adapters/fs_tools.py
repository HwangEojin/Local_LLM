"""내장 읽기 전용 파일 도구. 경로는 정책 엔진이 이미 정규화한 값으로 전달된다."""
from __future__ import annotations

import json
from pathlib import Path

from .. import __version__
from ..models import ToolError, ToolOutput, ToolSpec

VERSION = f"builtin-fs/{__version__}"
_PATH_ARGS = {"type": "object", "additionalProperties": False, "required": ["path"],
              "properties": {"path": {"type": "string", "minLength": 1, "maxLength": 1024}}}


def make_tools(max_bytes: int) -> dict[str, ToolSpec]:
    def read_file(args):
        p = Path(args["path"])
        if not p.is_file():
            raise ToolError(f"일반 파일이 아님: {p}")
        with open(p, "rb") as f:
            data = f.read(max_bytes + 1)
        return ToolOutput(data[:max_bytes], VERSION, truncated=len(data) > max_bytes)

    def list_dir(args):
        p = Path(args["path"])
        if not p.is_dir():
            raise ToolError(f"디렉터리가 아님: {p}")
        entries = [{"name": c.name, "type": "dir" if c.is_dir() else "file",
                    "symlink": c.is_symlink()} for c in sorted(p.iterdir())]
        return ToolOutput(json.dumps(entries, ensure_ascii=False, indent=1).encode(), VERSION)

    return {
        "fs.read_file": ToolSpec("fs.read_file", "read", _PATH_ARGS, {"path": "path"}, read_file, VERSION),
        "fs.list_dir": ToolSpec("fs.list_dir", "read", _PATH_ARGS, {"path": "path"}, list_dir, VERSION),
    }
