"""HTTP routes: the two required pages (Scans, Devices), the device detail view,
and the HTMX/JSON endpoints behind them.

Everything goes through the process-wide `engine` (hardware + live snapshot) and its
`store` (persistence). Cell formatting on the live table reuses each scanner's
`COLUMNS` functions via `engine.snapshot_rows()`, so the web table is identical to the
curses one. Active endpoints (scan start/stop, GATT browse) map one-to-one to the
engine's hardware-serialised methods.
"""
from __future__ import annotations

import os
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .engine import get_engine

_TEMPLATES = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

router = APIRouter()


def _engine():
    return get_engine()


# --- pages ------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
def scans_page(request: Request):
    eng = _engine()
    return _TEMPLATES.TemplateResponse(request, "scans.html", {
        "request": request, "kinds": eng.kinds(), "status": eng.status(),
    })


@router.get("/devices", response_class=HTMLResponse)
def devices_page(request: Request, search: str = "", transport: str = "",
                 fast_pair_only: bool = False):
    eng = _engine()
    devices = eng.list_devices(search=search or None, transport=transport or None,
                               fast_pair_only=fast_pair_only)
    return _TEMPLATES.TemplateResponse(request, "devices.html", {
        "request": request, "devices": devices, "search": search,
        "transport": transport, "fast_pair_only": fast_pair_only,
        "kinds": eng.kinds(),
    })


@router.get("/devices/{address}", response_class=HTMLResponse)
def device_detail(request: Request, address: str):
    eng = _engine()
    device = eng.get_device(address)
    if device is None:
        return HTMLResponse(f"<p>No device {address!r} in the store.</p>", status_code=404)
    can_browse = eng.is_scanning() and eng.status()["kind"] in ("ble", "fused")
    return _TEMPLATES.TemplateResponse(request, "device_detail.html", {
        "request": request, "d": device, "can_browse": can_browse,
    })


# --- scan control (HTMX) ----------------------------------------------------
@router.post("/api/scan/start", response_class=HTMLResponse)
def scan_start(request: Request, kind: str = Form("ble")):
    eng = _engine()
    try:
        eng.start_scan(kind)
    except Exception as err:                                    # noqa: BLE001
        eng._last_error = str(err)
    return _controls(request)


@router.post("/api/scan/stop", response_class=HTMLResponse)
def scan_stop(request: Request):
    eng = _engine()
    try:
        eng.stop_scan()
    except Exception as err:                                    # noqa: BLE001
        eng._last_error = str(err)
    return _controls(request)


def _controls(request: Request) -> HTMLResponse:
    eng = _engine()
    return _TEMPLATES.TemplateResponse(request, "_controls.html", {
        "request": request, "kinds": eng.kinds(), "status": eng.status(),
    })


@router.get("/api/scan/table", response_class=HTMLResponse)
def scan_table(request: Request):
    eng = _engine()
    return _TEMPLATES.TemplateResponse(request, "_scan_table.html", {
        "request": request, "columns": eng.columns(), "rows": eng.snapshot_rows(),
        "status": eng.status(),
    })


@router.get("/api/scan/status")
def scan_status():
    return JSONResponse(_engine().status())


@router.post("/api/scan/export")
def scan_export():
    eng = _engine()
    if eng._scanner is None:
        return JSONResponse({"error": "no scan running"}, status_code=400)
    path = eng._scanner.save_csv()
    return JSONResponse({"saved": path})


# --- device actions ---------------------------------------------------------
@router.post("/api/devices/{address}/gatt", response_class=HTMLResponse)
def browse_gatt(request: Request, address: str):
    eng = _engine()
    try:
        eng.browse_gatt(address)
        error = ""
    except Exception as err:                                    # noqa: BLE001
        error = str(err)
    device = eng.get_device(address)
    return _TEMPLATES.TemplateResponse(request, "_gatt_tree.html", {
        "request": request, "d": device, "error": error,
    })


# --- JSON API (parity with the MCP read tools) ------------------------------
@router.get("/api/devices")
def api_devices(search: str = "", transport: str = "", fast_pair_only: bool = False):
    return JSONResponse(_engine().list_devices(
        search=search or None, transport=transport or None, fast_pair_only=fast_pair_only))


@router.get("/api/devices/{address}")
def api_device(address: str):
    d = _engine().get_device(address)
    if d is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(d)


@router.get("/api/sessions")
def api_sessions():
    return JSONResponse(_engine().list_sessions())
