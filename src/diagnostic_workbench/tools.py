"""워크벤치가 직접 등록하는 로컬 도구. 인자는 정책 계층이 스키마·범위 검사를 마친 뒤 전달된다.

nmap.scan: 고정된 방식(TCP connect + 서비스 버전)만 허용하는 포트·서비스 탐색. 대상에 패킷을 보내는
능동 스캔이므로 위험도 'write' 로 등록한다(호출마다 승인 필요, LLM 탐색이 제안할 수 없음).
"""
from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from audit.models import ToolError, ToolOutput, ToolSpec

_DEFAULT_NMAP = Path(r"C:\Program Files (x86)\Nmap\nmap.exe")
MAX_PORTS = 1024
_PORTS = r"^\d{1,5}(-\d{1,5})?(,\d{1,5}(-\d{1,5})?){0,31}$"
_NMAP_ARGS = {
    "type": "object", "additionalProperties": False, "required": ["host"],
    "properties": {"host": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}$"},
                   "ports": {"type": "string", "pattern": _PORTS, "maxLength": 200}},
}


def _port_count(spec: str) -> int:
    total = 0
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        lo, hi = int(lo), int(hi or lo)
        if not 1 <= lo <= hi <= 65535:
            raise ToolError(f"잘못된 포트 범위: {part}")
        total += hi - lo + 1
    return total


def parse_nmap_xml(data: bytes) -> list[dict]:
    """열린 포트 목록: {"port", "proto", "service", "product"}. 출력은 신뢰할 수 없는 데이터로 다룬다."""
    out = []
    for port in ET.fromstring(data).iter("port"):
        state, svc = port.find("state"), port.find("service")
        if state is not None and state.get("state") == "open":
            out.append({"port": int(port.get("portid")), "proto": port.get("protocol", "tcp"),
                        "service": svc.get("name", "") if svc is not None else "",
                        "product": " ".join(filter(None, [svc.get("product"), svc.get("version")])) if svc is not None else ""})
    return out


def make_tools(cfg) -> dict[str, ToolSpec]:
    exe = cfg.nmap_path or shutil.which("nmap") or (str(_DEFAULT_NMAP) if _DEFAULT_NMAP.exists() else None)
    if not exe:
        return {}  # 설치되지 않은 도구는 등록하지 않는다
    timeout = 300

    def scan(args):
        ports = args.get("ports")
        if ports and _port_count(ports) > MAX_PORTS:
            raise ToolError(f"한 번에 스캔할 수 있는 포트는 {MAX_PORTS}개까지임")
        cmd = [exe, "-sT", "-sV", "--version-light", "-Pn", "-T3", "--host-timeout", f"{timeout - 30}s", "-oX", "-",
               *(["-p", ports] if ports else ["--top-ports", "100"]), args["host"]]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise ToolError(f"nmap 시간 초과 ({timeout}초)") from None
        except OSError as e:
            raise ToolError(f"nmap 을 실행할 수 없음: {e}") from None
        if p.returncode != 0:
            raise ToolError(f"nmap 종료 코드 {p.returncode}: {p.stderr[-300:].decode(errors='replace').strip()}")
        try:
            version = ET.fromstring(p.stdout).get("version", "")
        except ET.ParseError as e:
            raise ToolError(f"nmap 출력이 XML 이 아님: {e}") from None
        limit = cfg.settings.limits["max_evidence_bytes"]
        return ToolOutput(p.stdout[:limit], f"nmap/{version}", truncated=len(p.stdout) > limit)

    return {"nmap.scan": ToolSpec("nmap.scan", "write", _NMAP_ARGS, {"host": "host"}, scan, "nmap")}
