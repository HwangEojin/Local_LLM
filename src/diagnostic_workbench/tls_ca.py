"""프록시 MITM 용 루트 CA + 호스트별 리프 인증서 생성.

stdlib `ssl` 은 인증서를 '쓸' 수만 있고 '만들' 수는 없어서 `cryptography` 가 필요하다 — 이미
requirements.lock 에 고정돼 있는 패키지(mcp 의 전이 의존성)를 그대로 쓸 뿐, 새 의존성을 추가하는 게
아니다. import 는 이 모듈 안에서만 하므로(지연 import), `workbench proxy` 를 쓰지 않으면 CLI 의 나머지
명령은 cryptography 가 없어도 그대로 동작한다.

개인키(data/proxy_ca/)는 .gitignore 의 /data/ 에 포함돼 커밋되지 않는다. 이 CA 는 로컬 진단 전용이다 —
운영자의 OS/브라우저 신뢰 저장소에 설치해야 복호화가 되고, 설치하지 않으면 TLS 핸드셰이크가 그냥
실패한다(그 경우 프록시가 해당 연결만 닫고 평문 HTTP 쪽은 계속 동작).
"""
from __future__ import annotations

import datetime
import ssl
from pathlib import Path


class TlsCertError(Exception):
    """host 용 TLS 서버 컨텍스트를 준비하지 못함(인증서 생성·파일 읽기·체인 로드 중 하나가 실패)."""


def _ca_dir(cfg) -> Path:
    d = cfg.home / "data" / "proxy_ca"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ca_cert_path(cfg) -> Path:
    return _ca_dir(cfg) / "ca.crt"


def ensure_ca(cfg) -> tuple[Path, Path]:
    """없으면 루트 CA 를 한 번 만들어 둔다(5년). 있으면 그대로 반환."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    d = _ca_dir(cfg)
    cert_path, key_path = d / "ca.crt", d / "ca.key"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # CommonName 은 RFC 상 64자 제한 — 긴 한글 설명은 CN 밖(이 파일의 docstring)에 둔다.
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Local_LLM Workbench Proxy CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365 * 5))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(key_cert_sign=True, crl_sign=True, digital_signature=True,
                                         content_commitment=False, key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                           serialization.NoEncryption()))
    return cert_path, key_path


def leaf_cert_for(cfg, host: str) -> tuple[Path, Path]:
    """host 용 리프 인증서(루트 CA 서명, 1년, 캐시됨)를 돌려준다."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    d = _ca_dir(cfg) / "certs"
    d.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in ".-" else "_" for c in host)
    cert_path, key_path = d / f"{safe}.crt", d / f"{safe}.key"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path
    ca_cert_file, ca_key_file = ensure_ca(cfg)
    ca_key = serialization.load_pem_private_key(ca_key_file.read_bytes(), password=None)
    ca_cert = x509.load_pem_x509_certificate(ca_cert_file.read_bytes())
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(ca_cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                           serialization.NoEncryption()))
    return cert_path, key_path


def server_context_for(cfg, host: str) -> ssl.SSLContext:
    """host 용 리프 인증서로 TLS 서버 컨텍스트를 만든다. 원인이 cryptography 생성 실패든 파일 읽기든
    체인 로드든, 호출자(proxy.py)는 TlsCertError 하나만 보면 된다 — "이 호스트는 못 가로챈다"로 충분하다."""
    try:
        cert_path, key_path = leaf_cert_for(cfg, host)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert_path), str(key_path))
        return ctx
    except Exception as e:
        raise TlsCertError(str(e)) from e
