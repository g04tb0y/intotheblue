"""The single hardware-owning service behind the web UI and the MCP server.

Why a dedicated engine: the dongle (blatann) and the BlueZ adapter can each be
driven by **one** process, and within that process the dongle cannot scan and
connect at the same time (see `CLAUDE.md`). The curses CLI encodes this as
`_scan_interact_loop` — pause the scan, act on the *same* open `BleDevice`, resume.
This module lifts that invariant into a service so the web and MCP front-ends share
one owner of the hardware instead of each opening the dongle (a second open fails
with `rpc_h5_transport_state`).

Responsibilities:
  - own at most one active scanner (`ble` / `fused` / `classic`) and serialise every
    hardware operation behind one lock;
  - persist what the scan sees to the `store` on a background flush thread;
  - run connect-based GATT discovery (the one active BLE action) by pausing the scan,
    reusing `client.*`, persisting the tree, then resuming — never closing the dongle;
  - expose read helpers that reuse the existing passive analysers (`exposure`,
    `scan_fused.target_note`, `capabilities`) so nothing is reimplemented.

Importing this module pulls in blatann (via the scanners) but opens no hardware; the
dongle/adapter is touched only when a scan or a GATT browse actually starts.
"""
from __future__ import annotations

import threading
import time

import capabilities
import client
import exposure
import scan_classic
import scan_fused
import scan_le
import scan_live
import store as store_module

# kind -> (human label, columns, backends that can browse GATT over the dongle)
_KINDS = {
    "ble":     ("Live BLE scan — nRF52 dongle", scan_live.COLUMNS),
    "fused":   ("Live BLE scan — fused dongle + BlueZ", scan_fused.COLUMNS),
    "classic": ("Live Bluetooth Classic (BR/EDR) — host adapter", scan_classic.COLUMNS),
}
_BLE_KINDS = {"ble", "fused"}


class Engine:
    """Owns the active scanner and serialises all hardware access."""

    def __init__(self, store=None):
        self._store = store or store_module.get_store()
        self._lock = threading.Lock()           # serialises start/stop/browse
        self._scanner = None
        self._kind: str | None = None
        self._session_id: int | None = None
        self._flush_stop: threading.Event | None = None
        self._flush_thread: threading.Thread | None = None
        self._last_error = ""

    # --- introspection -----------------------------------------------------
    @property
    def store(self):
        return self._store

    def kinds(self) -> dict[str, str]:
        return {k: label for k, (label, _cols) in _KINDS.items()}

    def columns(self) -> list[str]:
        """Header names of the active scan's columns, or [] when idle."""
        if self._kind is None:
            return []
        return [h for h, _w, _fn in _KINDS[self._kind][1]]

    def is_scanning(self) -> bool:
        return self._scanner is not None and getattr(self._scanner, "is_scanning", False)

    def status(self) -> dict:
        snap = self._scanner.snapshot() if self._scanner is not None else []
        return {
            "kind": self._kind,
            "scanning": self.is_scanning(),
            "session_id": self._session_id,
            "devices": len(snap),
            "last_error": self._last_error,
        }

    # --- scan lifecycle ----------------------------------------------------
    def start_scan(self, kind: str) -> dict:
        """Open the chosen backend and begin scanning. One scan at a time."""
        if kind not in _KINDS:
            raise ValueError(f"unknown scan kind: {kind!r} (expected one of {list(_KINDS)})")
        with self._lock:
            if self._scanner is not None:
                raise RuntimeError(f"a '{self._kind}' scan is already running — stop it first")
            self._last_error = ""
            scanner = self._build_scanner(kind)
            self._scanner = scanner
            self._kind = kind
            self._session_id = self._store.open_session(kind)
            self._start_flush(scanner, self._session_id)
            return self.status()

    def _build_scanner(self, kind: str):
        if kind == "ble":
            sc = scan_live.LiveScanner(None)
            sc.open()
            sc.start()
            return sc
        if kind == "fused":
            sc = scan_fused.FusedScanner()
            sc.open()
            if not sc.dongle_ok:
                # Not fatal: fused degrades to BlueZ-only, but record why.
                self._last_error = f"dongle unavailable ({sc.dongle_error}) — BlueZ only"
            sc.start()
            return sc
        sc = scan_classic.ClassicScanner()
        sc.start()
        return sc

    def stop_scan(self) -> dict:
        with self._lock:
            if self._scanner is None:
                return self.status()
            self._stop_flush()
            self._flush_once(self._scanner, self._session_id)   # final snapshot
            try:
                self._scanner.close()
            except Exception as err:                            # noqa: BLE001
                self._last_error = f"close: {err}"
            finally:
                if self._session_id is not None:
                    self._store.close_session(self._session_id)
                self._scanner = None
                self._kind = None
                self._session_id = None
            return self.status()

    # --- live snapshot (rendered via the scanner's own COLUMNS) ------------
    def snapshot_rows(self) -> list[dict]:
        """Rows for the live table: address + formatted cells for the active kind.

        Reuses each scanner's `COLUMNS` cell functions so the web table matches the
        CLI exactly; also surfaces the `target_note` for BLE rows.
        """
        if self._scanner is None:
            return []
        cols = _KINDS[self._kind][1]
        rows = []
        for rec in self._scanner.snapshot():
            cells = [fn(rec) for _h, _w, fn in cols]
            note = scan_fused.target_note(rec) if self._kind in _BLE_KINDS else ""
            rows.append({
                "address": store_module.normalize_address(getattr(rec, "address", "")),
                "cells": cells,
                "target": note,
            })
        return rows

    # --- background persistence -------------------------------------------
    def _start_flush(self, scanner, session_id: int) -> None:
        self._flush_stop = threading.Event()
        self._flush_thread = threading.Thread(
            target=self._flush_loop, args=(scanner, session_id, self._flush_stop),
            daemon=True)
        self._flush_thread.start()

    def _stop_flush(self) -> None:
        if self._flush_stop is not None:
            self._flush_stop.set()
        if self._flush_thread is not None:
            self._flush_thread.join(timeout=5)
        self._flush_stop = None
        self._flush_thread = None

    def _flush_loop(self, scanner, session_id: int, stop: threading.Event) -> None:
        kind = self._kind
        while not stop.is_set():
            stop.wait(2.0)
            self._flush_once(scanner, session_id, kind)

    def _flush_once(self, scanner, session_id, kind: str | None = None) -> int:
        """Upsert every currently-known record into the store. Best-effort."""
        if scanner is None:
            return 0
        kind = kind or self._kind or "ble"
        n = 0
        try:
            for rec in scanner.snapshot():
                if self._store.upsert_device(rec, kind, session_id):
                    n += 1
        except Exception as err:                                # noqa: BLE001
            self._last_error = f"flush: {err}"
        return n

    # --- active action: connect + browse GATT (BLE only) -------------------
    def browse_gatt(self, address: str) -> dict:
        """Connect to a BLE device, discover its GATT, persist and return the tree.

        Pauses the scan and connects on the same open `BleDevice` (the CLI invariant),
        then resumes. Only valid during a `ble`/`fused` scan — the dongle is the only
        backend that performs a GATT connection here.
        """
        address = store_module.normalize_address(address)
        with self._lock:
            if self._scanner is None:
                raise RuntimeError("no scan running — start a BLE scan first")
            if self._kind not in _BLE_KINDS:
                raise RuntimeError("GATT browse needs the BLE dongle (start a 'ble' or 'fused' scan)")
            ble_device, peer = self._dongle_and_peer(address)
            if peer is None:
                raise RuntimeError(f"{address} not currently seen by the dongle "
                                   "(it may have rotated its address or moved out of range)")
            self._scanner.pause()
            try:
                result = self._discover(ble_device, peer)
            finally:
                self._scanner.resume()
        self._store.save_gatt(address, result["gatt"], self._session_id)
        if result.get("device_name"):
            self._store.set_name(address, result["device_name"])
        return result

    def _dongle_and_peer(self, address: str):
        """The open BleDevice and the blatann peer address for `address`, or (dev, None)."""
        if self._kind == "ble":
            dongle = self._scanner
        else:  # fused
            dongle = self._scanner.dongle
            if not getattr(self._scanner, "dongle_ok", False):
                raise RuntimeError("the dongle is not available in this fused scan")
        for rec in dongle.snapshot():
            if store_module.normalize_address(rec.address) == address and rec.peer is not None:
                return dongle.ble_device, rec.peer
        return dongle.ble_device, None

    @staticmethod
    def _is_transport_error(err: Exception) -> bool:
        """True for a wedged nRF52 serial-RPC transport (recoverable only by reopen).

        Matches any `NrfError.rpc_*` (rpc_no_response, rpc_invalid_state,
        rpc_h5_transport_state, ...) — the serial RPC layer to the connectivity chip.
        Deliberately distinct from a bare `NrfError.invalid_state` (no `rpc_` prefix),
        which is blatann's *characteristic-discovery* failure, not a transport wedge.
        """
        m = str(err).lower()
        return "nrferror.rpc_" in m or "rpc_h5_transport_state" in m

    @staticmethod
    def _release_stale_connections(ble_device) -> None:
        """Disconnect any peripheral blatann still holds open.

        We only ever hold one connection at a time, but if a previous browse did not
        tear down cleanly (e.g. the discovery wedge left the link up), blatann's
        `connect()` refuses the next attempt with 'Already connected'. Clearing the
        registry here makes Browse GATT repeatable instead of one-shot.
        """
        peripherals = dict(getattr(ble_device, "connected_peripherals", {}) or {})
        for periph in peripherals.values():
            try:
                periph.disconnect().wait(5, exception_on_timeout=False)
            except Exception:                                   # noqa: BLE001
                pass

    def _connect(self, ble_device, peer, attempts: int = 2):
        """Connect + discover, retrying once on a transient transport hiccup.

        `ble_gap_connect` can fail with `NrfError.rpc_no_response` when the dongle's
        serial RPC link glitches (often right after a dropped connection). A short
        retry clears a one-off; a truly wedged transport needs the dongle reopened,
        which only Stop→Start can do (we never close/reopen mid-session), so we say so.
        """
        # Release any connection a previous browse left dangling, else blatann raises
        # 'Already connected' and every subsequent Browse GATT fails.
        self._release_stale_connections(ble_device)
        last: Exception | None = None
        for i in range(attempts):
            try:
                conn = client.connect_and_discover(ble_device, peer)
                if conn is not None:
                    return conn
                last = RuntimeError("connection failed or timed out")
            except Exception as err:                            # noqa: BLE001
                last = err
                if not self._is_transport_error(err) or i == attempts - 1:
                    break
                time.sleep(1.5)
        if last is not None and self._is_transport_error(last):
            raise RuntimeError(
                f"dongle transport not responding ({last}). The nRF52 serial link is "
                "wedged — Stop the scan and Start it again to reopen the dongle (or give "
                "it a few seconds idle), then retry Browse GATT.")
        raise RuntimeError(f"connection failed: {last}")

    def _discover(self, ble_device, peer) -> dict:
        """Connect, walk the GATT database reading readable chars, disconnect."""
        conn = self._connect(ble_device, peer)
        try:
            # A dropped link during discovery leaves no services and makes every read
            # time out (5 s each). blatann logs the driver-thread cause as
            # NrfError.invalid_state on characteristic discovery — detect it here and
            # fail clearly rather than persist an empty tree over good data.
            # The blatann workaround (webui.blatann_patch) finalises discovery with
            # partial results instead of wedging, so an empty database is now the
            # genuine "nothing discoverable" case. An invalid_state can also be
            # transient, so give discovery one more pass before giving up.
            if not conn.database.services and conn.connected:
                conn.discover_services().wait(10, exception_on_timeout=False)
            if not conn.database.services:
                if conn.connected:
                    raise RuntimeError(
                        "GATT discovery populated no services (peer still connected) "
                        "even after a retry and the blatann partial-discovery "
                        "workaround. This device's GATT does not enumerate over the "
                        "dongle in this setup. If the transport then stops responding, "
                        "Stop→Start the scan to reopen the dongle.")
                raise RuntimeError(
                    "GATT discovery returned no services — the peer disconnected "
                    "during discovery. Bring it closer / keep it awake and retry.")
            tree = []
            device_name = ""
            for svc in conn.database.services:
                node = {
                    "uuid": str(svc.uuid),
                    "name": client.name_for_uuid(svc.uuid) or "",
                    "characteristics": [],
                }
                for ch in svc.characteristics:
                    val_hex, interp = "", ""
                    # Skip reads once the link is gone — otherwise each one blocks 5 s.
                    if ch.readable and conn.connected:
                        try:
                            _c, ev = ch.read().wait(5, exception_on_timeout=False)
                        except Exception:                       # noqa: BLE001
                            ev = None
                        if ev is not None and getattr(ev, "value", None) is not None:
                            val_hex = ev.value.hex()
                            interp = client._interpret(ch.uuid, ev.value) or ""
                            # GATT Device Name (0x2A00) — the only name some devices
                            # expose (they advertise none); backfill it on the record.
                            if client._u16(ch.uuid) == 0x2A00:
                                device_name = ev.value.decode("utf-8", "replace").strip()
                    node["characteristics"].append({
                        "uuid": str(ch.uuid),
                        "name": client.name_for_uuid(ch.uuid) or "",
                        "props": client._props(ch),
                        "value_hex": val_hex,
                        "value_interp": interp,
                    })
                tree.append(node)
            detected, _flags, _ns, _nc = capabilities.analyze(conn.database.services)
            capreport = capabilities.report(conn.database.services)
        finally:
            # Always tear the link down (not only when conn.connected looks True) —
            # a wedged discovery can leave blatann's view and the SoftDevice link out
            # of sync, and a leaked connection blocks the next browse.
            try:
                conn.disconnect().wait(5, exception_on_timeout=False)
            except Exception:
                pass
        # A service that reported zero characteristics is almost always the blatann
        # wedge finalised early by the workaround — flag it so the UI can offer the
        # BlueZ fallback to complete the enumeration.
        incomplete = [n["uuid"] for n in tree if not n["characteristics"]]
        return {"gatt": tree, "capabilities": [lbl for lbl, _note in detected],
                "capability_report": capreport, "device_name": device_name,
                "partial": bool(incomplete), "incomplete_services": incomplete}

    # --- read side (reuses the passive analysers) --------------------------
    def list_devices(self, **filters) -> list[dict]:
        devices = self._store.list_devices(**filters)
        for d in devices:
            d["target"] = scan_fused.target_note(store_module.row_to_record(d))
        return devices

    def get_device(self, address: str) -> dict | None:
        d = self._store.get_device(address)
        if d is None:
            return None
        rec = store_module.row_to_record(d)
        rec.services = {s["uuid"] for s in d["advertised_services"]}
        d["target"] = scan_fused.target_note(rec)
        d["exposure"] = exposure.assess(rec)
        d["exposure_report"] = exposure.report(rec)
        # Flag a GATT tree left incomplete by the blatann workaround (services with
        # no characteristics) so the detail page can warn and offer the BlueZ fallback.
        incomplete = [s["uuid"] for s in d.get("gatt", []) if not s["characteristics"]]
        d["gatt_partial"] = bool(incomplete)
        d["gatt_incomplete"] = incomplete
        return d

    def exposure_report(self, address: str) -> str | None:
        d = self._store.get_device(address)
        if d is None:
            return None
        rec = store_module.row_to_record(d)
        rec.services = {s["uuid"] for s in d["advertised_services"]}
        return exposure.report(rec)

    def list_sessions(self) -> list[dict]:
        return self._store.list_sessions()


# Module-level singleton: the one hardware owner for this process.
_engine: Engine | None = None
_engine_lock = threading.Lock()


def get_engine() -> Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = Engine()
        return _engine
