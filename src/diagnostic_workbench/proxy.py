"""자체 트래픽 레코딩 프록시(ARTEX 자체 MITM 프록시 대응).

HTTPS(CONNECT)도 실제로 복호화해서 기록한다: `tls_ca.leaf_cert_for()` 로 호스트별 리프 인증서를
발급받아 클라이언트와 TLS 핸드셰이크를 직접 맺고(= 중간자), 평문으로 복호화된 요청/응답을
evidence 로 남긴 뒤 실제 목적지로는 새 TLS 연결을 열어 전달한다. 운영자가 `tls_ca.ca_cert_path()`
의 루트 CA 인증서를 OS/브라우저 신뢰 저장소에 설치해야 클라이언트가 핸드셰이크를 받아들인다 —
설치하지 않으면 그 호스트 하나만 핸드셰이크 실패로 끊기고(평문 HTTP 쪽은 계속 동작), 운영자의
다른 트래픽에는 영향이 없다. 범위 밖 호스트는 HTTP 든 CONNECT 든 거부한다(CLAUDE.md §8 "리다이렉트
및 최종 네트워크 목적지를 다시 검증한다").
"""
from __future__ import annotations

import http.client
import socketserver
import ssl
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

from audit.models import ToolOutput

from . import evidence, tls_ca


def _in_scope(ctx, host: str) -> bool:
    return any(t.value == host for t in ctx.targets.values())


def _asset_id_for_host(ctx, host: str):
    for aid, t in ctx.targets.items():
        if t.value == host:
            return aid
    return None


def _record(method, url, headers, body, status, resp_headers, resp_body) -> bytes:
    return (f"{method} {url}\r\n" + "\r\n".join(f"{k}: {v}" for k, v in headers.items())).encode() + \
        b"\r\n\r\n" + (body or b"") + b"\r\n---response---\r\n" + f"{status}\r\n".encode() + \
        "\r\n".join(f"{k}: {v}" for k, v in resp_headers.items()).encode() + b"\r\n\r\n" + resp_body


def _fetch(method, url, headers, body):
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={k: v for k, v in headers.items() if k.lower() != "host"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read(1_000_000), resp.status, dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.read(1_000_000), e.code, dict(e.headers or {})


def _make_handler(ctx):
    class Handler(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            host, _, port = self.path.partition(":")
            if not _in_scope(ctx, host):
                self.send_error(403, "host not in scope")
                return
            try:
                cert_path, key_path = tls_ca.leaf_cert_for(ctx.cfg, host)
                tls_server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                tls_server_ctx.load_cert_chain(str(cert_path), str(key_path))
            except Exception as e:
                self.send_error(502, f"CA/cert error: {e}")
                return
            self.send_response(200, "Connection Established")
            self.end_headers()
            try:
                tls_sock = tls_server_ctx.wrap_socket(self.connection, server_side=True)
            except (ssl.SSLError, OSError):
                # 클라이언트가 이 워크벤치의 루트 CA 를 신뢰 저장소에 설치하지 않았을 때(가장 흔한
                # 원인). 이 호스트만 끊고, 다른 연결/평문 HTTP 는 그대로 동작한다.
                return
            try:
                self._mitm_https(tls_sock, host, int(port or 443))
            finally:
                try:
                    tls_sock.close()
                except OSError:
                    pass

        def _mitm_https(self, tls_sock, host, port):
            rfile = tls_sock.makefile("rb")
            request_line = rfile.readline().decode("iso-8859-1").rstrip("\r\n")
            if not request_line:
                return
            try:
                method, path, _version = request_line.split()
            except ValueError:
                return
            headers = http.client.parse_headers(rfile)
            length = int(headers.get("Content-Length", 0))
            body = rfile.read(length) if length else None
            netloc = host if port == 443 else f"{host}:{port}"
            url = f"https://{netloc}{path}"
            try:
                resp_body, status, resp_headers = _fetch(method, url, headers, body)
            except (urllib.error.URLError, OSError) as e:
                resp_body, status, resp_headers = str(e).encode(), 502, {}
            asset_id = _asset_id_for_host(ctx, host)
            if asset_id is not None:
                record = _record(method, url, headers, body, status, resp_headers, resp_body)
                evidence.store(ctx.conn, ctx.cfg.evidence_dir, run_id=ctx.run["id"], asset_id=asset_id,
                               requirement_id="proxy", tool="proxy.https", args={"method": method, "url": url},
                               output=ToolOutput(record, "proxy/1"), media_type="application/octet-stream", ext="bin")
                ctx.conn.commit()
            wfile = tls_sock.makefile("wb")
            reason = http.client.responses.get(status, "")
            wfile.write(f"HTTP/1.1 {status} {reason}\r\n".encode())
            for k, v in resp_headers.items():
                if k.lower() not in ("transfer-encoding", "content-length", "connection"):
                    wfile.write(f"{k}: {v}\r\n".encode())
            wfile.write(f"Content-Length: {len(resp_body)}\r\nConnection: close\r\n\r\n".encode())
            wfile.write(resp_body)
            wfile.flush()

        def _proxy_http(self, method):
            host = urlsplit(self.path).hostname or self.headers.get("Host", "").split(":")[0]
            if not _in_scope(ctx, host):
                self.send_error(403, "host not in scope")
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None
            try:
                resp_body, status, resp_headers = _fetch(method, self.path, self.headers, body)
            except (urllib.error.URLError, OSError) as e:
                self.send_error(502, str(e))
                return
            asset_id = _asset_id_for_host(ctx, host)
            if asset_id is not None:
                record = _record(method, self.path, self.headers, body, status, resp_headers, resp_body)
                evidence.store(ctx.conn, ctx.cfg.evidence_dir, run_id=ctx.run["id"], asset_id=asset_id,
                               requirement_id="proxy", tool="proxy.http", args={"method": method, "url": self.path},
                               output=ToolOutput(record, "proxy/1"), media_type="application/octet-stream", ext="bin")
                ctx.conn.commit()
            self.send_response(status)
            for k, v in resp_headers.items():
                if k.lower() not in ("transfer-encoding", "content-length", "connection"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)

        def do_GET(self):
            self._proxy_http("GET")

        def do_POST(self):
            self._proxy_http("POST")

        def log_message(self, *args):
            pass

    return Handler


def serve(ctx, port: int, *, server_cls=socketserver.ThreadingTCPServer) -> None:
    """포그라운드로 블로킹 실행한다(Ctrl+C 로 종료). HTTP/HTTPS 요청·응답 모두 evidence 로 남는다.
    HTTPS 는 `tls_ca.ca_cert_path(ctx.cfg)` 의 루트 CA 를 클라이언트가 신뢰해야 복호화된다."""
    server = server_cls(("127.0.0.1", port), _make_handler(ctx))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
