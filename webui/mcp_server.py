"""MCP server exposing the bench to agents, over the same engine + store.

Mounted into the FastAPI app as a streamable-HTTP sub-app (shared process, so there
is no second owner of the dongle). Tools split into two groups, stated plainly in
each docstring:
  - **read-only** (DB / passive advertising analysis / event-log queries): send
    nothing on the air;
  - **active** (`scan_start`/`scan_stop`/`browse_gatt`): drive the radio — a GATT
    browse opens an LE connection, exactly like the web button.

The tools are thin wrappers over `webui.engine.Engine`; all identification and
persistence logic lives there and in `store`, never duplicated here.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .engine import Engine, get_engine


def build_mcp(engine: Engine | None = None) -> FastMCP:
    """Create the FastMCP server bound to `engine` (the process-wide one by default)."""
    engine = engine or get_engine()
    mcp = FastMCP("intotheblue")
    # Serve the MCP endpoint at the mount root (we mount this sub-app under /mcp),
    # otherwise the path would double to /mcp/mcp.
    mcp.settings.streamable_http_path = "/"

    # --- read-only tools ---------------------------------------------------
    @mcp.tool()
    def list_devices(search: str = "", transport: str = "",
                     fast_pair_only: bool = False) -> list[dict]:
        """List persisted devices (read-only). Optional substring `search` (name /
        address / manufacturer / vendor), `transport` filter (ble/fused/classic/le),
        and `fast_pair_only`. Returns stored enrichment plus a `target` note."""
        return engine.list_devices(search=search or None, transport=transport or None,
                                   fast_pair_only=fast_pair_only)

    @mcp.tool()
    def get_device(address: str) -> dict:
        """Full stored view of one device (read-only): enrichment, advertised
        services, GATT tree, sighting history, and a passive exposure assessment."""
        d = engine.get_device(address)
        if d is None:
            return {"error": f"no device {address!r} in the store"}
        return d

    @mcp.tool()
    def exposure_report(address: str) -> str:
        """Passive exposure report for a device, from advertising already stored
        (read-only, transmits nothing)."""
        r = engine.exposure_report(address)
        return r or f"no device {address!r} in the store"

    @mcp.tool()
    def capabilities(address: str) -> dict:
        """Capability fingerprint for a device (read-only): detected capabilities,
        attack-surface flags, and a DFU/OTA exposure note, computed from the GATT tree
        already stored (transmits nothing). Browse the device first to populate it."""
        c = engine.device_capabilities(address)
        return c or {"error": f"no GATT stored for {address!r} — browse it first"}

    @mcp.tool()
    def list_scan_sessions() -> list[dict]:
        """List scan sessions (read-only), newest first, with device counts."""
        return engine.list_sessions()

    @mcp.tool()
    def scan_status() -> dict:
        """Current scan state (read-only): active kind, scanning flag, device count."""
        return engine.status()

    @mcp.tool()
    def scan_snapshot() -> list[dict]:
        """Live rows of the active scan (read-only): address, formatted cells, target."""
        return engine.snapshot_rows()

    @mcp.tool()
    def query_events(event: str = "", device: str = "", action: str = "",
                     limit: int = 100) -> list[dict]:
        """Query the structured event log (read-only; reads the JSONL history, transmits
        nothing). Filter by exact `event` (scan_start/scan_stop/browse_gatt/gatt_*/
        browse_gatt_bluez/classic_profiles), `device` address, or `action` substring;
        newest first. Each event carries ts, outcome (ok/error/start), boundary and
        transport."""
        return engine.query_events(event=event, device=device, action=action, limit=limit)

    # --- active tools (drive the radio) ------------------------------------
    @mcp.tool()
    def scan_start(kind: str = "ble") -> dict:
        """ACTIVE: open a backend and begin scanning. `kind` is ble | fused | classic.
        One scan at a time (the process owns the hardware)."""
        return engine.start_scan(kind)

    @mcp.tool()
    def scan_stop() -> dict:
        """ACTIVE: stop the running scan and release the hardware."""
        return engine.stop_scan()

    @mcp.tool()
    def browse_gatt(address: str) -> dict:
        """ACTIVE: open an LE connection to `address`, discover and read its GATT,
        persist it, and return the service/characteristic tree. Requires a running
        ble/fused scan; pauses it for the connection."""
        return engine.browse_gatt(address)

    @mcp.tool()
    def gatt_connect(address: str) -> dict:
        """ACTIVE: open a held-open GATT interaction session (connect and keep the link
        for subsequent reads/writes/subscribes). Pauses the scan until gatt_disconnect
        or the idle timeout. Requires a running ble/fused scan."""
        return engine.gatt_connect(address)

    @mcp.tool()
    def gatt_disconnect(address: str = "") -> dict:
        """Close the held-open GATT session and resume the scan."""
        return engine.gatt_disconnect(address)

    @mcp.tool()
    def gatt_read(address: str, char_uuid: str) -> dict:
        """ACTIVE: read one characteristic on the open session (on demand)."""
        return engine.gatt_read(address, char_uuid)

    @mcp.tool()
    def gatt_write(address: str, char_uuid: str, value: str, mode: str = "auto") -> dict:
        """ACTIVE: write to one characteristic on the open session. `mode` is
        hex | text | auto (parsed exactly like the CLI)."""
        return engine.gatt_write(address, char_uuid, value, mode)

    @mcp.tool()
    def gatt_subscribe(address: str, char_uuid: str) -> dict:
        """ACTIVE: subscribe to notifications on one characteristic; they buffer and are
        drained by gatt_notifications."""
        return engine.gatt_subscribe(address, char_uuid)

    @mcp.tool()
    def gatt_unsubscribe(address: str, char_uuid: str) -> dict:
        """Stop notifications on one characteristic."""
        return engine.gatt_unsubscribe(address, char_uuid)

    @mcp.tool()
    def gatt_notifications(address: str) -> dict:
        """Read-only: drain notifications buffered since the last call on the session."""
        return engine.gatt_notifications(address)

    @mcp.tool()
    def classic_profiles(address: str) -> dict:
        """ACTIVE: read the device's Classic SDP profiles over BlueZ (bluetoothctl info)
        and classify them. Pauses a running fused/classic scan for the query."""
        return engine.classic_profiles(address)

    return mcp
