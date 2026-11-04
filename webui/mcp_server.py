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
                     fast_pair_only: bool = False, seen_within_seconds: float = 0,
                     current_session_only: bool = False) -> list[dict]:
        """List persisted devices (read-only). Optional substring `search` (name /
        address / manufacturer / vendor), `transport` filter (ble/fused/classic/le),
        `fast_pair_only`, `seen_within_seconds` (keep only devices seen that recently;
        0 = all history), and `current_session_only` (only devices seen in the current
        or most recent scan). Returns stored enrichment plus a `target` note."""
        import time
        since = (time.time() - seen_within_seconds) if seen_within_seconds else None
        session_id = engine.current_session_id() if current_session_only else None
        return engine.list_devices(search=search or None, transport=transport or None,
                                   fast_pair_only=fast_pair_only, since=since,
                                   session_id=session_id)

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
    def fast_pair_profile(address: str) -> dict:
        """Fast Pair GATT profile for a device (read-only): which Fast Pair
        characteristics (…1234 Key-based Pairing, …1235 Passkey, …1236 Account Key,
        …1237 Additional Data) are present and their props, from the stored GATT tree
        (transmits nothing). Browse the device first to populate it."""
        p = engine.fast_pair_profile(address)
        return p or {"error": f"no Fast Pair (0xFE2C) service in the stored GATT for "
                              f"{address!r} — browse it first, or not a Fast Pair Provider"}

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

    @mcp.tool()
    def prune_devices(older_than_seconds: float = 604800) -> dict:
        """DESTRUCTIVE (store, not radio): delete persisted devices not seen within
        `older_than_seconds` (default 7 days), with their sightings/advertised services/
        GATT tree. Frees history that the `list_devices` recency filters only hide.
        Returns the number removed. Transmits nothing."""
        import time
        removed = engine.prune_devices(time.time() - older_than_seconds)
        return {"removed": removed, "older_than_seconds": older_than_seconds}

    # --- active tools (drive the radio) ------------------------------------
    @mcp.tool()
    def scan_start(kind: str = "ble") -> dict:
        """ACTIVE: open a backend and begin scanning. `kind` is ble | fused | classic |
        sniffle (CC2652P/Sonoff BLE sniffer). One scan at a time (the process owns the
        hardware)."""
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
    def gatt_pair(address: str, method: str = "dongle") -> dict:
        """ACTIVE: attempt to bond with `address`. method=dongle (central-side Just Works
        pairing on the open GATT session, needs gatt_connect first) | bluez (bluetoothctl
        pair over hci0). Returns the outcome; a refused bond is a valid result, not an error."""
        return engine.gatt_pair(address, method)

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

    @mcp.tool()
    def fastpair_handshake(address: str, provider_public: str = "", model_id: str = "",
                           allow_network: bool = False) -> dict:
        """ACTIVE: run the Fast Pair Key-based Pairing handshake on the open GATT session
        (needs gatt_connect first). Writes an AES-encrypted Key-based Pairing request to
        …1234 and awaits the Provider's notified response, then decrypts it.
        Supply the Provider's anti-spoofing public key via `provider_public` (hex/base64 —
        e.g. what `emulate.py --fast-pair-gatt` prints), or give `model_id` to resolve it
        from fastpair_keys.yaml (and, with `allow_network`, the opt-in Google lookup).
        A response that does not decrypt, or no notify, is a valid finding, not an error."""
        return engine.fastpair_handshake(address, provider_public, model_id=model_id,
                                         allow_network=allow_network)

    @mcp.tool()
    def fastpair_resolve_key(model_id: str, allow_network: bool = False) -> dict:
        """Resolve a Fast Pair anti-spoofing PUBLIC key by Model ID. Offline file lookup
        (`fastpair_keys.yaml`) is passive; `allow_network=True` enables an opt-in NETWORK
        fetch from Google's device-metadata endpoint (needs $INTOTHEBLUE_FASTPAIR_API_KEY),
        cached back to the file. Returns {model_id, public_key_hex|None, source, detail}.
        Not a radio op."""
        return engine.fastpair_resolve_key(model_id, allow_network)

    @mcp.tool()
    def fastpair_stress(address: str, variant: str) -> dict:
        """ACTIVE: write a malformed Key-based Pairing payload to …1234 and report the
        Provider's reaction (notify / silence / dropped link) — authorized robustness
        testing. `variant` is one of: unencrypted-zero, wrong-type, too-short, too-long,
        random-16. Needs an open GATT session (gatt_connect first)."""
        return engine.fastpair_stress(address, variant)

    # --- Sniffle connection follow (Phase 3c) ------------------------------
    @mcp.tool()
    def list_connections(peripheral: str = "", session_id: int = 0,
                         seen_within_seconds: float = 0.0) -> list[dict]:
        """List persisted followed BLE connections (read-only, transmits nothing), most
        recent first. Each row pairs a central (InitA, the phone) with a peripheral (AdvA,
        the device) — the phone↔device relationship Sniffle observed — with access address,
        link params, encrypted/terminated flags and packet counts. Optional filters: a
        `peripheral` address, a `session_id`, or a `seen_within_seconds` recency window."""
        since = (time.time() - seen_within_seconds) if seen_within_seconds else None
        return engine.list_connections(peripheral=peripheral or None,
                                       session_id=session_id or None, since=since)

    @mcp.tool()
    def sniffle_follow(address: str) -> dict:
        """RECEIVE-ONLY RADIO: retarget the running Sniffle scan to follow `address`'s next
        connection (scan→select→follow). Reconfigures the Sonoff to CONN_FOLLOW on that
        address; the sniffer still only listens (transmits nothing). Needs a running
        `sniffle` scan. The operator must then trigger the device's (re)connection for a
        CONNECT_IND to be captured; captured connections appear via scan_snapshot /
        list_connections."""
        return engine.sniffle_follow(address)

    @mcp.tool()
    def sniffle_stop_follow() -> dict:
        """Revert a following Sniffle scan back to the advertising scan (same Sonoff)."""
        return engine.sniffle_stop_follow()

    @mcp.tool()
    def sniffle_export_pcap(aa: str) -> dict:
        """Export a currently-followed connection's buffered packets to a PCAP for
        Wireshark (CONNECT_IND + followed data; the ATT/GATT dissects when unencrypted).
        `aa` is the connection's access address (hex, e.g. '0xBCE222F4'). Works only while
        that connection is being followed (the store keeps no raw packets). Transmits
        nothing. Returns {path, packets, aa}."""
        return engine.sniffle_export_pcap(aa)

    return mcp
