"""자체 트래픽 레코딩 프록시(ARTEX 자체 MITM 프록시 대응, 축소판).

실제 MITM(TLS 가로채기, CA 인증서 설치·배포)은 하지 않는다 — 평문 HTTP 요청/응답만 기록하고,
HTTPS(CONNECT)는 내용을 들여다보지 않고 범위 안의 호스트인지만 확인한 뒤 그대로 터널링한다.
운영자가 브라우저/도구의 프록시 설정을 이 서버로 돌려야 쓸 수 있다. Burp 같은 전용 MCP 도구가
떠 있으면 그걸 쓰는 게 낫다 — 이건 그게 없을 때의 최소 대체 경로다(CLAUDE.md §8 "리다이렉트 및
최종 네트워크 목적지를 다시 검증한다": 범위 밖 호스트는 HTTP 든 CONNECT 든 거부한다).
"""
from __future__ import annotations

import select
import socket
import socketserver
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

from audit.models import ToolOutput

from . import evidence


def _in_scope(ctx, host: str) -> bool:
    return any(t.value == host for t in ctx.targets.values())


def _asset_id_for_host(ctx, host: str):
    for aid, t in ctx.targets.items():
        if t.value == host:
            return aid
    return None


def _make_handler(ctx):
    class Handler(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            host, _, port = self.path.partition(":")
            if not _in_scope(ctx, host):
                self.send_error(403, "host not in scope")
                return
            try:
                upstream = socket.create_connection((host, int(port or 443)), timeout=10)
            except OSError as e:
                self.send_error(502, str(e))
                return
            self.send_response(200, "Connection Established")
            self.end_headers()
            self._tunnel(upstream)

        def _tunnel(self, upstream):
            sockets = [self.connection, upstream]
            while True:
                try:
                    ready, _, _ = select.select(sockets, [], [], 5)
                except OSError:
                    break
                if not ready:
                    continue
                done = False
                for s in ready:
                    try:
                        data = s.recv(65536)
                    except OSError:
                        done = True
                        break
                    if not data:
                        done = True
                        break
                    (upstream if s is self.connection else self.connection).sendall(data)
                if done:
                    break
            upstream.close()

        def _proxy_http(self, method):
            host = urlsplit(self.path).hostname or self.headers.get("Host", "").split(":")[0]
            if not _in_scope(ctx, host):
                self.send_error(403, "host not in scope")
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None
            req = urllib.request.Request(self.path, data=body, method=method,
                                         headers={k: v for k, v in self.headers.items() if k.lower() != "host"})
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    resp_body, status, resp_headers = resp.read(1_000_000), resp.status, dict(resp.headers)
            except urllib.error.HTTPError as e:
                resp_body, status, resp_headers = e.read(1_000_000), e.code, dict(e.headers or {})
            except (urllib.error.URLError, OSError) as e:
                self.send_error(502, str(e))
                return
            asset_id = _asset_id_for_host(ctx, host)
            if asset_id is not None:
                record = (f"{method} {self.path}\r\n" +
                         "\r\n".join(f"{k}: {v}" for k, v in self.headers.items())).encode() + b"\r\n\r\n" + \
                         (body or b"") + b"\r\n---response---\r\n" + f"{status}\r\n".encode() + \
                         "\r\n".join(f"{k}: {v}" for k, v in resp_headers.items()).encode() + b"\r\n\r\n" + resp_body
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
    """포그라운드로 블로킹 실행한다(Ctrl+C 로 종료). HTTP 요청/응답은 evidence 로 남고, HTTPS(CONNECT)는
    범위 확인만 하고 내용은 안 본 채 터널링한다."""
    server = server_cls(("127.0.0.1", port), _make_handler(ctx))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
