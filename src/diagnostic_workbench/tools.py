"""워크벤치가 직접 등록하는 로컬 도구. 인자는 정책 계층이 스키마·범위 검사를 마친 뒤 전달된다.

nmap.scan: 고정된 방식(TCP connect + 서비스 버전)만 허용하는 포트·서비스 탐색. 대상에 패킷을 보내는
능동 스캔이므로 위험도 'write' 로 등록한다(호출마다 승인 필요, LLM 탐색이 제안할 수 없음).

tls.inspect: TLS 핸드셰이크를 몇 번 직접 걸어 인증서·취약 프로토콜 허용 여부를 본다. 대상에 실제로
연결을 여러 번 여는 능동 점검이므로 nmap.scan 과 같은 이유로 'write'.

nmap.ssl_scan: nmap 에 내장된 NSE 스크립트(ssl-enum-ciphers, ssl-heartbleed, ssl-ccs-injection)로 약한
암호 알고리즘과 알려진 TLS 취약점을 본다. 호출자가 스크립트를 고를 수 없게 고정 목록만 쓴다(임의
--script 인자를 받으면 과도한 공격 행위로 이어질 수 있음).

tls.renegotiation: testssl.sh(-R) 로 안전하지 않은 TLS 재협상 허용 여부를 본다. nmap NSE 에는 이 점검이
없고 Python stdlib ssl 에는 재협상을 거는 API 자체가 없어 전용 스캐너가 필요했다. Git-for-Windows MSYS 에
없는 hexdump/pkill 은 data/tools/hexdump-shim 의 대체 스크립트로 보완한다(§7A 참고, 판정에 쓰는 두 호출
형태만 정확히 구현 — 실제 GNU hexdump 출력과 바이트 단위로 대조 확인함).
"""
from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from audit.models import ToolError, ToolOutput, ToolSpec

from . import tls_inspect

_KILL_POLL_S = 0.2


def _run_cancelable(cmd, timeout: float, stop_file: Path, **popen_kwargs) -> subprocess.CompletedProcess:
    """subprocess.run(capture_output=True, timeout=...) 대신 쓴다. Kill Switch(전체 중지 스위치)가 켜지면
    기다리는 동안에도(이미 시작된 프로세스라도) 즉시 종료한다(CLAUDE.md §8 Kill Switch 동작: '진행 중
    작업의 취소 요청', '취소되지 않는 격리된 도구 프로세스 종료'). 출력은 별도 스레드의 communicate() 로
    받아 파이프 버퍼가 가득 차 멈추는 문제를 피한다.
    """
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                         **popen_kwargs)
    result: dict = {}

    def _drain():
        result["out"], result["err"] = p.communicate()

    t = threading.Thread(target=_drain, name="tool-subprocess-wait", daemon=True)
    t.start()
    deadline = time.monotonic() + timeout
    while t.is_alive():
        if stop_file.exists():
            p.kill()
            t.join(timeout=5)
            raise ToolError(f"전체 중지 스위치로 실행 중인 프로세스를 종료함 ({stop_file})")
        if time.monotonic() > deadline:
            p.kill()
            t.join(timeout=5)
            raise subprocess.TimeoutExpired(cmd, timeout)
        t.join(timeout=_KILL_POLL_S)
    return subprocess.CompletedProcess(cmd, p.returncode, result.get("out", b""), result.get("err", b""))

_SHIM_DIR = Path(__file__).resolve().parents[2] / "data" / "tools" / "hexdump-shim"

_DEFAULT_NMAP = Path(r"C:\Program Files (x86)\Nmap\nmap.exe")
MAX_PORTS = 1024
_PORTS = r"^\d{1,5}(-\d{1,5})?(,\d{1,5}(-\d{1,5})?){0,31}$"
_NMAP_ARGS = {
    "type": "object", "additionalProperties": False, "required": ["host"],
    "properties": {"host": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}$"},
                   "ports": {"type": "string", "pattern": _PORTS, "maxLength": 200}},
}
_TLS_ARGS = {
    "type": "object", "additionalProperties": False, "required": ["host"],
    "properties": {"host": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}$"},
                   "port": {"type": "integer", "minimum": 1, "maximum": 65535}},
}
_SSL_SCRIPTS = "ssl-enum-ciphers,ssl-heartbleed,ssl-ccs-injection"  # 고정 목록, 인자로 바꿀 수 없음


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
    tools: dict[str, ToolSpec] = {}
    timeout = 300

    def inspect_tls(args):
        try:
            result = tls_inspect.inspect(args["host"], args.get("port", 443))
        except (OSError, ssl.SSLError, ValueError) as e:
            raise ToolError(f"TLS 점검 실패: {e}") from None
        data = json.dumps(result, ensure_ascii=False).encode("utf-8")
        return ToolOutput(data, "stdlib-ssl", truncated=False)

    tools["tls.inspect"] = ToolSpec("tls.inspect", "write", _TLS_ARGS, {"host": "host"}, inspect_tls, "stdlib-ssl")

    testssl, bash_exe = cfg.testssl_path, shutil.which("bash")
    if testssl and Path(testssl).is_file() and bash_exe:
        def renegotiation_scan(args):
            env = {**os.environ, "PATH": f"{_SHIM_DIR}{os.pathsep}{os.environ.get('PATH', '')}"}
            cmd = [bash_exe, testssl, "--warnings", "off", "--color", "0", "--connect-timeout", "10", "-R",
                   f"{args['host']}:{args.get('port', 443)}"]
            try:
                p = _run_cancelable(cmd, timeout, cfg.stop_file, env=env)
            except subprocess.TimeoutExpired:
                raise ToolError(f"testssl.sh 시간 초과 ({timeout}초)") from None
            except OSError as e:
                raise ToolError(f"testssl.sh 를 실행할 수 없음: {e}") from None
            if p.returncode != 0:
                raise ToolError(f"testssl.sh 종료 코드 {p.returncode}: {p.stderr[-300:].decode(errors='replace').strip()}")
            limit = cfg.settings.limits["max_evidence_bytes"]
            return ToolOutput(p.stdout[:limit], "testssl.sh/3.2.4", truncated=len(p.stdout) > limit)

        tools["tls.renegotiation"] = ToolSpec("tls.renegotiation", "write", _TLS_ARGS, {"host": "host"},
                                              renegotiation_scan, "testssl.sh")

    exe = cfg.nmap_path or shutil.which("nmap") or (str(_DEFAULT_NMAP) if _DEFAULT_NMAP.exists() else None)
    if not exe:
        return tools  # nmap 이 없으면 nmap 기반 도구만 등록하지 않는다

    def scan(args):
        ports = args.get("ports")
        if ports and _port_count(ports) > MAX_PORTS:
            raise ToolError(f"한 번에 스캔할 수 있는 포트는 {MAX_PORTS}개까지임")
        cmd = [exe, "-sT", "-sV", "--version-light", "-Pn", "-T3", "--host-timeout", f"{timeout - 30}s", "-oX", "-",
               *(["-p", ports] if ports else ["--top-ports", "100"]), args["host"]]
        try:
            p = _run_cancelable(cmd, timeout, cfg.stop_file)
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

    def ssl_scan(args):
        port = args.get("port", 443)
        cmd = [exe, "-Pn", "-p", str(port), "--script", _SSL_SCRIPTS, "-T3",
               "--host-timeout", f"{timeout - 30}s", "-oN", "-", args["host"]]
        try:
            p = _run_cancelable(cmd, timeout, cfg.stop_file)
        except subprocess.TimeoutExpired:
            raise ToolError(f"nmap 시간 초과 ({timeout}초)") from None
        except OSError as e:
            raise ToolError(f"nmap 을 실행할 수 없음: {e}") from None
        if p.returncode != 0:
            raise ToolError(f"nmap 종료 코드 {p.returncode}: {p.stderr[-300:].decode(errors='replace').strip()}")
        limit = cfg.settings.limits["max_evidence_bytes"]
        return ToolOutput(p.stdout[:limit], "nmap/nse", truncated=len(p.stdout) > limit)

    tools["nmap.scan"] = ToolSpec("nmap.scan", "write", _NMAP_ARGS, {"host": "host"}, scan, "nmap")
    tools["nmap.ssl_scan"] = ToolSpec("nmap.ssl_scan", "write", _TLS_ARGS, {"host": "host"}, ssl_scan, "nmap")
    return tools
