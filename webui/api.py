"""HTTP routes: the two required pages (Scans, Devices), the device detail view,
and the HTMX/JSON endpoints behind them.

Everything goes through the process-wide `engine` (hardware + live snapshot) and its
`store` (persistence). Cell formatting on the live table reuses each scanner's
`COLUMNS` functions via `engine.snapshot_rows()`, so the web table is identical to the
curses one. Active endpoints (scan start/stop, GATT browse) map one-to-one to the
engine's hardware-serialised methods.
"""
from __future__ import annotations

import json
import os
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .engine import get_engine

_TEMPLATES = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def _localtime(epoch) -> str:
    """Format a stored epoch (float seconds) as a readable local timestamp.

    The store keeps `last_seen` as raw epoch (format only at display); an absent or
    zero value renders blank rather than "1970-01-01".
    """
    try:
        ts = float(epoch)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _clocktime(epoch) -> str:
    """Compact HH:MM:SS for the log tail/table (the date is usually today)."""
    try:
        ts = float(epoch)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    return time.strftime("%H:%M:%S", time.localtime(ts))


_TEMPLATES.env.filters["localtime"] = _localtime
_TEMPLATES.env.filters["clocktime"] = _clocktime

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


# Recency presets for the Devices list: label -> window in seconds (None = all history).
# The store keeps every device forever; these only scope the *view* to recent sightings.
_RECENT_WINDOWS: dict[str, int | None] = {
    "": None, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800,
}
# Prune presets: delete devices not seen within this window (older than now - window).
_PRUNE_WINDOWS: dict[str, int] = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}


def _recent_cutoff(recent: str) -> float | None:
    """Epoch floor for a recency preset, or None for 'all'."""
    seconds = _RECENT_WINDOWS.get(recent)
    return (time.time() - seconds) if seconds else None


@router.get("/devices", response_class=HTMLResponse)
def devices_page(request: Request, search: str = "", transport: str = "",
                 fast_pair_only: bool = False, recent: str = "24h", session: bool = False,
                 pruned: int | None = None):
    eng = _engine()
    since = _recent_cutoff(recent)
    session_id = eng.current_session_id() if session else None
    devices = eng.list_devices(search=search or None, transport=transport or None,
                               fast_pair_only=fast_pair_only, since=since,
                               session_id=session_id)
    return _TEMPLATES.TemplateResponse(request, "devices.html", {
        "request": request, "devices": devices, "search": search,
        "transport": transport, "fast_pair_only": fast_pair_only,
        "kinds": eng.kinds(), "recent": recent, "recent_windows": _RECENT_WINDOWS,
        "session": session, "session_scoped": session_id is not None,
        "prune_windows": _PRUNE_WINDOWS, "pruned": pruned,
    })


@router.post("/api/devices/prune")
def devices_prune(older_than: str = Form("7d")):
    """DESTRUCTIVE: delete persisted devices not seen within the chosen window, then
    bounce back to the Devices list with a count. Gated behind an explicit form action
    (a confirm dialog in the UI); the recency/session filters only hide, this removes."""
    seconds = _PRUNE_WINDOWS.get(older_than)
    if seconds is None:
        return RedirectResponse("/devices", status_code=303)
    removed = _engine().prune_devices(time.time() - seconds)
    return RedirectResponse(f"/devices?pruned={removed}", status_code=303)


@router.get("/devices/{address}", response_class=HTMLResponse)
def device_detail(request: Request, address: str, bare: bool = False):
    """Device detail page. `bare=1` drops the header/tab-strip/footer chrome so the page
    can be embedded as an in-page device tab (an iframe) by the single-page tab strip."""
    eng = _engine()
    device = eng.get_device(address)
    if device is None:
        return HTMLResponse(f"<p>No device {address!r} in the store.</p>", status_code=404)
    can_browse = eng.is_scanning() and eng.status()["kind"] in ("ble", "fused")
    return _TEMPLATES.TemplateResponse(request, "device_detail.html", {
        "request": request, "d": device, "can_browse": can_browse, "bare": bare,
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
    return _render_gatt(request, address, error=error, tree_changed=True)


@router.post("/api/devices/{address}/gatt/bluez", response_class=HTMLResponse)
def browse_gatt_bluez(request: Request, address: str):
    """Fallback GATT enumeration over BlueZ (hci0) — completes what the dongle couldn't."""
    eng = _engine()
    try:
        eng.browse_gatt_bluez(address)
        error = ""
    except Exception as err:                                    # noqa: BLE001
        error = str(err)
    return _render_gatt(request, address, error=error, tree_changed=True)


# --- GATT interaction session (Task 1) --------------------------------------
def _render_gatt(request: Request, address: str, error: str = "",
                 action: str = "", tree_changed: bool = False) -> HTMLResponse:
    """Re-render the interactive GATT fragment with the current stored tree + session.

    When `tree_changed` is set (an enumeration-changing op persisted a new tree), the
    response carries an `HX-Trigger: gatt-updated` header so the detail page's
    Capabilities panel — computed from the *stored* tree and rendered once at page load
    — re-fetches itself instead of going stale until a full reload.
    """
    eng = _engine()
    device = eng.get_device(address)
    can_browse = eng.is_scanning() and eng.status()["kind"] in ("ble", "fused")
    resp = _TEMPLATES.TemplateResponse(request, "_gatt_tree.html", {
        "request": request, "d": device, "error": error, "action": action,
        "can_browse": can_browse,
    })
    if tree_changed and not error:
        resp.headers["HX-Trigger"] = "gatt-updated"
    return resp


@router.post("/api/devices/{address}/gatt/session", response_class=HTMLResponse)
def gatt_session_open(request: Request, address: str):
    """ACTIVE: open a held-open interaction session (connect, keep the link)."""
    eng = _engine()
    try:
        eng.gatt_connect(address)
        error, action = "", "Session open — connection held for read/write/subscribe."
    except Exception as err:                                    # noqa: BLE001
        error, action = str(err), ""
    return _render_gatt(request, address, error=error, action=action, tree_changed=True)


@router.post("/api/devices/{address}/gatt/session/close", response_class=HTMLResponse)
def gatt_session_close(request: Request, address: str):
    """Close the interaction session and resume the scan."""
    eng = _engine()
    try:
        eng.gatt_disconnect(address)
        error, action = "", "Session closed — scan resumed."
    except Exception as err:                                    # noqa: BLE001
        error, action = str(err), ""
    return _render_gatt(request, address, error=error, action=action)


@router.post("/api/devices/{address}/gatt/pair", response_class=HTMLResponse)
def gatt_pair(request: Request, address: str, method: str = Form("dongle")):
    """ACTIVE: attempt a bond — method=dongle (central-side, needs an open session) or
    bluez (bluetoothctl pair over hci0). Reports the outcome; a refusal is a valid result."""
    eng = _engine()
    try:
        r = eng.gatt_pair(address, method)
        error = ""
        action = f"Pair ({r['method']}): {r['status']}" + (" — bonded" if r["bonded"] else "")
    except Exception as err:                                    # noqa: BLE001
        error, action = str(err), ""
    return _render_gatt(request, address, error=error, action=action)


@router.post("/api/devices/{address}/gatt/fastpair-handshake", response_class=HTMLResponse)
def fastpair_handshake(request: Request, address: str, provider_public: str = Form(""),
                       model_id: str = Form(""), allow_network: str = Form("")):
    """ACTIVE: run the Fast Pair Key-based Pairing handshake on the open session.

    Needs the Provider's anti-spoofing public key: paste it (hex/base64) in
    `provider_public`, or give a `model_id` to resolve it from `fastpair_keys.yaml` (and,
    with `allow_network`, the opt-in Google lookup). A response that does not decrypt, or
    no notify, is reported as a finding — not an error."""
    eng = _engine()
    try:
        r = eng.fastpair_handshake(address, provider_public, model_id=model_id,
                                   allow_network=bool(allow_network))
        error = ""
        action = "Fast Pair handshake: " + r["detail"]
    except Exception as err:                                    # noqa: BLE001
        error, action = str(err), ""
    return _render_gatt(request, address, error=error, action=action)


@router.post("/api/devices/{address}/gatt/fastpair-stress", response_class=HTMLResponse)
def fastpair_stress(request: Request, address: str, variant: str = Form(...)):
    """ACTIVE: write a malformed Key-based Pairing payload and report the Provider's reaction."""
    eng = _engine()
    try:
        r = eng.fastpair_stress(address, variant)
        error = ""
        action = f"Fast Pair stress ({r['variant']}): {r['detail']}"
    except Exception as err:                                    # noqa: BLE001
        error, action = str(err), ""
    return _render_gatt(request, address, error=error, action=action)


@router.post("/api/devices/{address}/gatt/read", response_class=HTMLResponse)
def gatt_read(request: Request, address: str, char: str = Form(...)):
    """Read one characteristic on the open session (on demand)."""
    eng = _engine()
    try:
        r = eng.gatt_read(address, char)
        error = ""
        action = f"Read {char}: {r['value_hex'] or '(empty)'}"
        if r.get("value_interp"):
            action += f" → {r['value_interp']}"
    except Exception as err:                                    # noqa: BLE001
        error, action = str(err), ""
    return _render_gatt(request, address, error=error, action=action)


@router.post("/api/devices/{address}/gatt/write", response_class=HTMLResponse)
def gatt_write(request: Request, address: str, char: str = Form(...),
               value: str = Form(""), mode: str = Form("auto")):
    """ACTIVE: write a value to one characteristic on the open session."""
    eng = _engine()
    try:
        r = eng.gatt_write(address, char, value, mode)
        error = ""
        action = f"Wrote {r['bytes']} byte(s) to {char}: {r['wrote_hex']}"
    except Exception as err:                                    # noqa: BLE001
        error, action = str(err), ""
    return _render_gatt(request, address, error=error, action=action)


@router.post("/api/devices/{address}/gatt/subscribe", response_class=HTMLResponse)
def gatt_subscribe(request: Request, address: str, char: str = Form(...)):
    """ACTIVE: subscribe to notifications on one characteristic."""
    eng = _engine()
    try:
        eng.gatt_subscribe(address, char)
        error, action = "", f"Subscribed to {char} — notifications below."
    except Exception as err:                                    # noqa: BLE001
        error, action = str(err), ""
    return _render_gatt(request, address, error=error, action=action)


@router.post("/api/devices/{address}/gatt/unsubscribe", response_class=HTMLResponse)
def gatt_unsubscribe(request: Request, address: str, char: str = Form(...)):
    eng = _engine()
    try:
        eng.gatt_unsubscribe(address, char)
        error, action = "", f"Unsubscribed from {char}."
    except Exception as err:                                    # noqa: BLE001
        error, action = str(err), ""
    return _render_gatt(request, address, error=error, action=action)


@router.get("/api/devices/{address}/gatt/notifications", response_class=HTMLResponse)
def gatt_notifications(request: Request, address: str):
    """Drain buffered notifications for the session (polled; appended to the log)."""
    eng = _engine()
    data = eng.gatt_notifications(address)
    return _TEMPLATES.TemplateResponse(request, "_notifications.html", {
        "request": request, "notifications": data["notifications"],
    })


# --- Capabilities (Task 2) --------------------------------------------------
@router.get("/api/devices/{address}/capabilities", response_class=HTMLResponse)
def capabilities_view(request: Request, address: str):
    """Capability fingerprint fragment, computed from the stored GATT tree (passive)."""
    eng = _engine()
    device = eng.get_device(address)
    return _TEMPLATES.TemplateResponse(request, "_capabilities.html", {
        "request": request, "d": device,
    })


# --- Fast Pair GATT profile (passive — TODO item 1) -------------------------
@router.get("/api/devices/{address}/fastpair", response_class=HTMLResponse)
def fast_pair_view(request: Request, address: str):
    """Fast Pair GATT profile fragment, computed from the stored tree (passive)."""
    eng = _engine()
    device = eng.get_device(address)
    return _TEMPLATES.TemplateResponse(request, "_fastpair.html", {
        "request": request, "d": device,
    })


# --- Classic SDP profiles (Task 5) ------------------------------------------
@router.post("/api/devices/{address}/classic", response_class=HTMLResponse)
def classic_profiles(request: Request, address: str):
    """ACTIVE: read SDP profiles over BlueZ (bluetoothctl info) and classify them."""
    eng = _engine()
    try:
        profiles = eng.classic_profiles(address)
        error = ""
    except Exception as err:                                    # noqa: BLE001
        profiles, error = None, str(err)
    return _TEMPLATES.TemplateResponse(request, "_classic_profiles.html", {
        "request": request, "address": address, "profiles": profiles, "error": error,
    })


# --- Logs (structured JSONL event history) ----------------------------------
def _distinct_events(eng) -> list[str]:
    """Event names seen in the log, for the filter dropdown (best-effort)."""
    return sorted({e.get("event", "") for e in eng.query_events(limit=2000) if e.get("event")})


@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, event: str = "", device: str = "", action: str = "",
              limit: int = 200):
    """Browse the JSONL event history: filter by event / device / action, export."""
    eng = _engine()
    events = eng.query_events(event=event, device=device, action=action, limit=limit)
    return _TEMPLATES.TemplateResponse(request, "logs.html", {
        "request": request, "events": events, "event": event, "device": device,
        "action": action, "limit": limit, "event_names": _distinct_events(eng),
    })


@router.get("/api/logs/table", response_class=HTMLResponse)
def logs_table(request: Request, event: str = "", device: str = "", action: str = "",
               limit: int = 200):
    eng = _engine()
    events = eng.query_events(event=event, device=device, action=action, limit=limit)
    return _TEMPLATES.TemplateResponse(request, "_logs_table.html", {
        "request": request, "events": events,
    })


@router.get("/api/logs/tail", response_class=HTMLResponse)
def logs_tail(request: Request):
    """The always-on footer tail — the most recent events, oldest-first."""
    return _TEMPLATES.TemplateResponse(request, "_log_tail.html", {
        "request": request, "events": _engine().tail_events(10),
    })


@router.get("/api/logs/export")
def logs_export(event: str = "", device: str = "", action: str = "", limit: int = 10000):
    """Download the filtered event set as JSON Lines (one object per line)."""
    events = _engine().query_events(event=event, device=device, action=action, limit=limit)
    body = "\n".join(json.dumps(e, default=str) for e in events) + ("\n" if events else "")
    fname = f"intotheblue_events_{int(time.time())}.jsonl"
    return Response(content=body, media_type="application/x-ndjson",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


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
