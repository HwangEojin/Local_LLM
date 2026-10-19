"""tls.renegotiation(testssl.sh 연동): 실제 테스트 서버에 실제 testssl.sh 를 돌려 WEB-SER-037 오버레이가
참조하는 'not vulnerable (OK)' 문구가 정말로 나오는지 확인한다(이 PC 에 설치 안 돼 있으면 건너뜀).
"""
import shutil
import socket
import ssl
import threading
import unittest
from pathlib import Path

from diagnostic_workbench import config, tools

ROOT = Path(__file__).resolve().parents[2]
TESTSSL = ROOT / "data" / "tools" / "testssl.sh" / "testssl.sh"
FIXTURES = ROOT / "tests" / "fixtures" / "tls"


class _Server:
    def __init__(self, certfile, keyfile):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile, keyfile)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.bind(("127.0.0.1", 0))
        raw.listen(10)
        self.port = raw.getsockname()[1]
        self._sock = ctx.wrap_socket(raw, server_side=True)
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop:
            try:
                self._sock.settimeout(0.3)
                conn, _ = self._sock.accept()
                try:
                    conn.recv(4096)
                    conn.send(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                except (socket.timeout, ssl.SSLError, OSError):
                    pass
                conn.close()
            except (socket.timeout, ssl.SSLError, OSError):
                continue

    def close(self):
        self._stop = True
        self._sock.close()


@unittest.skipUnless(TESTSSL.is_file() and shutil.which("bash"), "testssl.sh 또는 bash 가 이 PC 에 없음")
class TlsRenegotiationTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _Server(str(FIXTURES / "cert.pem"), str(FIXTURES / "key.pem"))

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_modern_server_not_vulnerable(self):
        cfg = config.load(None)
        cfg.testssl_path = str(TESTSSL)
        reg = tools.make_tools(cfg)
        self.assertIn("tls.renegotiation", reg)
        out = reg["tls.renegotiation"].executor({"host": "127.0.0.1", "port": self.server.port})
        text = out.data.decode(errors="replace")
        self.assertIn("Secure Renegotiation (RFC 5746)           supported (OK)", text)
        self.assertIn("Secure Client-Initiated Renegotiation     not vulnerable (OK)", text)


if __name__ == "__main__":
    unittest.main()
