"""로컬 웹 UI 백엔드. `workbench web` 으로 띄운다(src/diagnostic_workbench/cli.py 의 cmd_web 참고).

새 진단 로직을 두지 않는다 — diagnostic_workbench 모듈을 그대로 부르는 HTTP 어댑터일 뿐이다.
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import deps
from .routers import auth as auth_router
from .routers import environment as environment_router
from .routers import findings as findings_router
from .routers import runs as runs_router

deps.get_cfg()  # .env 를 지금 읽어 둔다 — auth.py 는 환경 변수를 직접 읽으므로 로그인 전에 필요함

app = FastAPI(title="Workbench Web API", docs_url="/api/docs", openapi_url="/api/openapi.json")

_origins = [o.strip() for o in os.environ.get("WORKBENCH_WEB_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(auth_router.router)
app.include_router(environment_router.router)
app.include_router(runs_router.router)
app.include_router(findings_router.router)


@app.get("/api/health")
def health():
    return {"ok": True}
