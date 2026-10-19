"""TLS 핸드셰이크·인증서 점검: stdlib ssl/socket 만으로, 새 의존성 없이.

인증서 무결성(WEB-SER-030)과 취약한 프로토콜 허용 여부(WEB-SER-034)의 자동 점검 (A) 오버레이가 쓰는 증적을
만든다. cipher 전체 목록(035)·Heartbleed 류(036)·재협상(037)은 stdlib 로 정확히 확인할 수 없어 다루지 않는다
(전용 스캐너가 필요 — CLAUDE.md §1.5, 새 의존성 결정은 별도로 한다).
"""
from __future__ import annotations

import datetime
import socket
import ssl
import tempfile
from pathlib import Path

_WEAK_VERSIONS = {"SSLv3": ssl.TLSVersion.SSLv3, "TLSv1": ssl.TLSVersion.TLSv1, "TLSv1_1": ssl.TLSVersion.TLSv1_1}


def fetch_cert_pem(host: str, port: int, timeout: float) -> str:
    """검증 없이 서버 인증서를 PEM 으로 받아온다(자체서명·만료된 인증서도 받아야 하므로)."""
    return ssl.get_server_certificate((host, port), timeout=timeout)


def analyze_cert(pem: str, host: str) -> dict:
    """ssl._test_decode_cert(비공개 API, CPython 테스트에서도 쓰는 안정적인 헬퍼)로 파싱한다.

    getpeercert() 는 검증된 연결에서만 값을 주므로, 검증 없이 받은 PEM 을 파일로 떠서 디코딩한다.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
        f.write(pem)
        path = f.name
    try:
        cert = ssl._ssl._test_decode_cert(path)
    finally:
        Path(path).unlink(missing_ok=True)

    def cn(rdns):
        for rdn in rdns:
            for key, value in rdn:
                if key == "commonName":
                    return value
        return None

    subject_cn, issuer_cn = cn(cert.get("subject", ())), cn(cert.get("issuer", ()))
    not_after = datetime.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=datetime.UTC)
    names = {v for k, v in cert.get("subjectAltName", ()) if k == "DNS"} | ({subject_cn} if subject_cn else set())
    return {
        "subject_cn": subject_cn, "issuer_cn": issuer_cn, "not_after": cert["notAfter"],
        "expired": not_after < datetime.datetime.now(datetime.UTC),
        "self_signed": subject_cn is not None and subject_cn == issuer_cn,
        "hostname_match": any(_matches(n, host) for n in names) if names else False,
    }


def _matches(pattern: str, host: str) -> bool:
    if pattern.startswith("*."):
        return host.count(".") >= pattern.count(".") and host.split(".", 1)[-1] == pattern[2:]
    return pattern.lower() == host.lower()


def probe_weak_protocols(host: str, port: int, timeout: float) -> dict:
    """각 취약 버전으로 핸드셰이크를 강제 시도한다. 성공하면 서버가 그 버전을 허용한다는 뜻(취약)."""
    out = {}
    for name, version in _WEAK_VERSIONS.items():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        try:
            ctx.minimum_version = ctx.maximum_version = version
            # 최신 OpenSSL 의 기본 보안 레벨은 이 구버전 프로토콜에 쓰이는 cipher 를 전부 막는다.
            # 낮추지 않으면 실제로 취약한 서버에도 우리 쪽에서 cipher 협상이 안 돼 'rejected' 로
            # 잘못 보고한다(이 프로브가 서버 설정을 확인하는 것이지, 우리 클라이언트의 기본값을 확인하는게 아니다).
            ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        except (ValueError, OSError):
            out[name] = "client_unsupported"  # 이 환경의 OpenSSL 이 아예 못 거는 버전 — 서버 쪽은 확인 못함
            continue
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock, \
                 ctx.wrap_socket(sock, server_hostname=host):
                out[name] = "accepted"
        except (ssl.SSLError, OSError):
            out[name] = "rejected"
    return out


def inspect(host: str, port: int, timeout: float = 10.0) -> dict:
    pem = fetch_cert_pem(host, port, timeout)
    return {"host": host, "port": port, "cert": analyze_cert(pem, host),
            "weak_protocols": probe_weak_protocols(host, port, timeout)}
