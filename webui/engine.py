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

    def _discover(self, ble_device, peer) -> dict:
        """Connect, walk the GATT database reading readable chars, disconnect."""
        conn = client.connect_and_discover(ble_device, peer)
        if conn is None:
            raise RuntimeError("connection failed or timed out")
        try:
            tree = []
            for svc in conn.database.services:
                node = {
                    "uuid": str(svc.uuid),
                    "name": client.name_for_uuid(svc.uuid) or "",
                    "characteristics": [],
                }
                for ch in svc.characteristics:
                    val_hex, interp = "", ""
                    if ch.readable:
                        _c, ev = ch.read().wait(5, exception_on_timeout=False)
                        if ev is not None:
                            val_hex = ev.value.hex()
                            interp = client._interpret(ch.uuid, ev.value) or ""
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
            try:
                conn.disconnect().wait()
            except Exception:
                pass
        return {"gatt": tree, "capabilities": [lbl for lbl, _note in detected],
                "capability_report": capreport}

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
