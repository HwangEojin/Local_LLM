"""tls.inspect 자동 점검 (A) 오버레이용 증적 도구: 실제 TLS 서버에 연결해 인증서·취약 프로토콜 탐지를 확인.

로컬에 자체서명 인증서로 TLS 서버를 띄우고 127.0.0.1 로 실제 핸드셰이크를 건다(모의/목이 아님).
최소 TLS 버전을 1.2 로 고정한 서버라 TLSv1/TLSv1_1 강제 접속은 거부돼야 한다.
"""
import socket
import ssl
import threading
import unittest
from pathlib import Path

from diagnostic_workbench import tls_inspect

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tls"


class _Server:
    """자체서명 인증서로 127.0.0.1 에 TLS 를 서빙하고, 연결마다 아무것도 안 하고 닫는다."""

    def __init__(self, certfile, keyfile, min_version=ssl.TLSVersion.TLSv1_2, ciphers=None):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile, keyfile)
        ctx.minimum_version = min_version
        if ciphers:  # 구버전 프로토콜을 흉내 내려면 서버도 그 시절 cipher 를 열어야 한다
            ctx.set_ciphers(ciphers)
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.bind(("127.0.0.1", 0))
        raw.listen(5)
        self.port = raw.getsockname()[1]
        self._sock = ctx.wrap_socket(raw, server_side=True)
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop:
            try:
                self._sock.settimeout(0.2)
                conn, _ = self._sock.accept()
                conn.close()
            except (socket.timeout, ssl.SSLError, OSError):
                continue

    def close(self):
        self._stop = True
        self._sock.close()


class TLSInspectLiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _Server(str(FIXTURES / "cert.pem"), str(FIXTURES / "key.pem"))

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_self_signed_cert_and_hostname_match_detected(self):
        result = tls_inspect.inspect("127.0.0.1", self.server.port, timeout=3)
        self.assertTrue(result["cert"]["self_signed"])
        self.assertTrue(result["cert"]["hostname_match"])
        self.assertFalse(result["cert"]["expired"])

    def test_weak_protocols_rejected_by_tls12_minimum_server(self):
        result = tls_inspect.inspect("127.0.0.1", self.server.port, timeout=3)
        self.assertIn(result["weak_protocols"]["TLSv1"], ("rejected", "client_unsupported"))
        self.assertIn(result["weak_protocols"]["TLSv1_1"], ("rejected", "client_unsupported"))


class TLSInspectDetectsAcceptedWeakProtocol(unittest.TestCase):
    """서버가 TLSv1_1 까지 허용하면 'accepted'로 정확히 보고하는지(항상 rejected 를 내는 거짓양성 방지)."""

    @classmethod
    def setUpClass(cls):
        cls.server = _Server(str(FIXTURES / "cert.pem"), str(FIXTURES / "key.pem"),
                             min_version=ssl.TLSVersion.TLSv1_1, ciphers="DEFAULT:@SECLEVEL=0")

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_tlsv1_1_reported_accepted(self):
        result = tls_inspect.inspect("127.0.0.1", self.server.port, timeout=3)
        self.assertEqual(result["weak_protocols"]["TLSv1_1"], "accepted")


class AnalyzeCertHostnameMismatch(unittest.TestCase):
    """인증서는 유효하지만 CN/SAN 이 접속한 호스트명과 다른 경우를 놓치지 않는지."""

    def test_mismatch_detected(self):
        pem = Path(FIXTURES / "cert.pem").read_text(encoding="utf-8")
        result = tls_inspect.analyze_cert(pem, "not-the-cert-host.example")
        self.assertFalse(result["hostname_match"])
        self.assertTrue(result["self_signed"])


if __name__ == "__main__":
    unittest.main()
