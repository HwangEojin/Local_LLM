"""공식 Python SDK(mcp==1.27.0) 기반 MCP 클라이언트를 동기 방식으로 감싼 모듈.

결과는 신뢰할 수 없는 데이터다: 직렬화하여 증거로 저장할 뿐 명령으로 해석하지 않는다.
"""
from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import json
import os
import re
import tempfile
import threading
from contextlib import asynccontextmanager
from datetime import timedelta

from . import schemas
from .models import ToolError, ToolOutput


def _find_tool_error(e: BaseException) -> ToolError | None:
    """anyio 태스크 그룹이 감싼 예외 안에서 ToolError 를 찾는다."""
    if isinstance(e, ToolError):
        return e
    for sub in getattr(e, "exceptions", ()):
        found = _find_tool_error(sub)
        if found:
            return found
    return None


def _env(srv: dict) -> dict:
    from mcp.client.stdio import get_default_environment
    env = get_default_environment()  # 최소 환경변수만 사용하고, 비밀값은 명시적인 env_from 으로만 주입한다
    for name, host_var in srv.get("env_from", {}).items():
        if host_var not in os.environ:
            raise ToolError(f"MCP 서버에 필요한 환경변수 {host_var} 이(가) 설정되지 않음")
        env[name] = os.environ[host_var]
    return env


@asynccontextmanager
async def _session(srv: dict):
    from mcp import ClientSession, StdioServerParameters
    # 서버 stderr 는 임시 파일로 받는다: 터미널을 어지럽히지 않고, 리디렉션된 환경(fileno 없음)에서도 실행되며,
    # 연결 실패 때 원인(서버가 출력한 오류)을 메시지에 덧붙일 수 있다.
    errlog = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")
    try:
        if srv["transport"] == "stdio":
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(command=srv["command"], args=srv.get("args", []), env=_env(srv))
            transport = stdio_client(params, errlog=errlog)
        else:
            from mcp.client.sse import sse_client
            transport = sse_client(srv["url"], timeout=srv["timeout_s"])
        async with transport as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                yield session, init
    except Exception as e:  # CancelledError(시간 초과)는 BaseException 이라 그대로 전파된다
        inner = _find_tool_error(e)
        if inner is not None:  # 우리가 만든 오류(스키마 거부, 오류 응답 등)는 그대로 전달
            raise inner from None
        errlog.flush()
        errlog.seek(0)
        tail = errlog.read()[-300:].strip()
        if tail:
            raise ToolError(f"MCP 오류: {type(e).__name__}: {e} | 서버 stderr: {tail}") from None
        raise
    finally:
        errlog.close()


def _run(coro_fn, srv: dict):
    async def guarded():
        return await asyncio.wait_for(coro_fn(), timeout=srv["timeout_s"])
    try:
        return asyncio.run(guarded())
    except ToolError:
        raise
    except (TimeoutError, asyncio.TimeoutError):
        raise ToolError(f"MCP 시간 초과 ({srv['timeout_s']}초)") from None
    except BaseException as e:  # SDK는 anyio 태스크 그룹에서 ExceptionGroup을 발생시킨다
        if isinstance(e, KeyboardInterrupt):
            raise
        found = _find_tool_error(e)
        if found is not None:
            raise found from None
        inner = e.exceptions[0] if isinstance(e, BaseExceptionGroup) and e.exceptions else e
        if "timed out" in str(inner).lower():  # SDK 자체의 읽기 시간 제한
            raise ToolError(f"MCP 시간 초과 ({srv['timeout_s']}초)") from None
        raise ToolError(f"MCP 오류: {type(inner).__name__}: {inner}") from None


def list_tools(srv: dict) -> dict:
    async def go():
        async with _session(srv) as (s, init):
            res = await s.list_tools()
            return {"server": f"{init.serverInfo.name}/{init.serverInfo.version}",
                    "tools": [{"name": t.name, "description": t.description or "",
                               "inputSchema": t.inputSchema} for t in res.tools]}
    return _run(go, srv)


async def _do_call(s, init, srv: dict, tool: str, args: dict, max_bytes: int, offered=None) -> ToolOutput:
    offered = offered or {t.name: t for t in (await s.list_tools()).tools}
    if tool not in offered:
        raise ToolError(f"서버가 도구 '{tool}' 을(를) 제공하지 않음")
    try:
        errs = schemas.validate(args, offered[tool].inputSchema)
    except schemas.SchemaError as e:
        raise ToolError(f"도구 '{tool}' 의 서버 스키마를 지원하지 않음 (fail-closed): {e}") from None
    if errs:
        raise ToolError(f"서버 스키마가 인자를 거부함: {'; '.join(errs)}")
    res = await s.call_tool(tool, args, read_timeout_seconds=timedelta(seconds=srv["timeout_s"]))
    payload = {"isError": bool(res.isError),
               "content": [c.model_dump(mode="json") for c in res.content],
               "structuredContent": res.structuredContent}
    data = json.dumps(payload, ensure_ascii=False).encode()
    if res.isError:
        raise ToolError(f"MCP 도구가 오류를 반환함: {data[:500].decode(errors='replace')}")
    body = "".join(c.get("text", "") for c in payload["content"] if c.get("type") == "text")
    for pat in srv.get("error_patterns", []):
        if re.search(pat, body):
            raise ToolError(f"서버가 오류 응답을 반환함(오류 플래그 없이 본문으로): {body[:300]}")
    return ToolOutput(data[:max_bytes], f"{init.serverInfo.name}/{init.serverInfo.version}",
                      truncated=len(data) > max_bytes)


class _Persistent:
    """상태를 유지하는 서버(Frida, Playwright 등)용: 한 프로세스 안에서 연결 하나를 열어 두고 호출들이 공유한다.

    별도 스레드의 이벤트 루프가 세션을 소유한다(anyio 는 세션을 연 태스크에서 닫아야 하므로).
    프로세스가 끝나면 close_all() 이 서버를 종료한다.
    """

    def __init__(self, srv: dict):
        self.srv = srv
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self.state = None
        self.stop = None
        self.thread = threading.Thread(target=self._thread, name="mcp-persistent", daemon=True)
        self.thread.start()
        if not self.ready.wait(self.srv["timeout_s"]):
            self.close()
            raise ToolError(f"MCP 시간 초과 ({srv['timeout_s']}초): 서버 연결을 열지 못함")
        if self.error is not None:
            raise ToolError(f"MCP 오류: {type(self.error).__name__}: {self.error}") from None

    def _thread(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        finally:
            self.loop.close()

    async def _main(self):
        self.stop = asyncio.Event()
        try:
            async with _session(self.srv) as (s, init):
                offered = {t.name: t for t in (await s.list_tools()).tools}
                self.state = (s, init, offered)
                self.ready.set()
                await self.stop.wait()
        except BaseException as e:
            self.error = e
            self.ready.set()

    def call(self, tool, args, max_bytes) -> ToolOutput:
        s, init, offered = self.state
        fut = asyncio.run_coroutine_threadsafe(_do_call(s, init, self.srv, tool, args, max_bytes, offered), self.loop)
        try:
            return fut.result(timeout=self.srv["timeout_s"] + 5)
        except ToolError:
            raise
        except (TimeoutError, concurrent.futures.TimeoutError):
            fut.cancel()
            raise ToolError(f"MCP 시간 초과 ({self.srv['timeout_s']}초)") from None
        except Exception as e:
            if "timed out" in str(e).lower():  # SDK 자체의 읽기 시간 제한
                raise ToolError(f"MCP 시간 초과 ({self.srv['timeout_s']}초)") from None
            raise ToolError(f"MCP 오류: {type(e).__name__}: {e}") from None

    def close(self):
        if self.stop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.stop.set)
        self.thread.join(timeout=10)


_POOL: dict[str, _Persistent] = {}
_POOL_LOCK = threading.Lock()


def _pool_key(srv: dict) -> str:
    return json.dumps(srv, sort_keys=True, ensure_ascii=False)


def close_all():
    """연결을 유지 중인 MCP 서버를 모두 종료한다(프로세스 종료 시 자동 호출)."""
    with _POOL_LOCK:
        conns = list(_POOL.values())
        _POOL.clear()
    for c in conns:
        c.close()


atexit.register(close_all)


def call_tool(srv: dict, tool: str, args: dict, max_bytes: int) -> ToolOutput:
    if srv.get("persistent"):
        with _POOL_LOCK:
            conn = _POOL.get(_pool_key(srv))
            if conn is None or not conn.thread.is_alive():
                conn = _POOL[_pool_key(srv)] = _Persistent(srv)
        return conn.call(tool, args, max_bytes)

    async def go():
        async with _session(srv) as (s, init):
            return await _do_call(s, init, srv, tool, args, max_bytes)
    return _run(go, srv)
