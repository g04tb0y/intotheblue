"""FastAPI application: web UI + JSON API + mounted MCP server, one hardware engine.

Lifespan note (the one real gotcha when mounting an MCP server, confirmed against the
MCP SDK ASGI docs): a mounted sub-app's own lifespan does **not** run, so the host app
must drive the MCP session manager itself — we enter `mcp.session_manager.run()` in the
FastAPI lifespan. The same lifespan is where we stop any running scan on shutdown so the
dongle is always released.
"""
from __future__ import annotations

import contextlib
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router
from .engine import get_engine
from .mcp_server import build_mcp

_STATIC = os.path.join(os.path.dirname(__file__), "static")

# Build the MCP server (and its streamable-HTTP Starlette sub-app) once at import.
_mcp = build_mcp(get_engine())
_mcp_app = _mcp.streamable_http_app()


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    # Drive the mounted MCP server's session manager (its own lifespan won't run).
    async with _mcp.session_manager.run():
        yield
    # Shutdown: make sure the hardware is released even if a scan is still active.
    with contextlib.suppress(Exception):
        get_engine().stop_scan()


def create_app() -> FastAPI:
    app = FastAPI(title="intotheblue", lifespan=_lifespan)
    app.include_router(router)
    app.mount("/mcp", _mcp_app)
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")
    return app


app = create_app()
