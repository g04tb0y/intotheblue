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

import contextlib
import os
import threading
import time
from types import SimpleNamespace

import capabilities
import client
import eventlog as eventlog_module
import fastpair
import fastpair_keys
import exposure
import gatt_bluez
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

    def __init__(self, store=None, events=None):
        self._store = store or store_module.get_store()
        self._events = events or eventlog_module.get_eventlog()
        self._lock = threading.Lock()           # serialises start/stop/browse
        self._scanner = None
        self._kind: str | None = None
        self._session_id: int | None = None
        self._flush_stop: threading.Event | None = None
        self._flush_thread: threading.Thread | None = None
        self._last_error = ""
        # Held-open GATT interaction session (Task 1): the dongle cannot scan and
        # connect at once, so a session pauses the scan and keeps ONE peer connected
        # across read/write/subscribe requests instead of reconnecting per operation.
        self._gatt_addr: str | None = None
        self._gatt_peer = None                   # the connected blatann peer
        self._gatt_notifs: dict[str, list[dict]] = {}   # char uuid -> buffered notifications
        self._gatt_subscribed: set[str] = set()  # char uuids currently subscribed
        self._gatt_deadline: float = 0.0         # idle auto-close time (monotonic-ish)
        self._gatt_pair_status: dict | None = None  # last Pair outcome (for UI/MCP)
        self._gatt_fp_status: dict | None = None   # last Fast Pair handshake/stress outcome

    # Idle timeout for a held-open GATT session: a session pins the scan paused and
    # holds an LE link, so it must not leak if the browser tab is closed mid-session.
    GATT_SESSION_IDLE = 60.0

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

    # --- structured event log ----------------------------------------------
    @contextlib.contextmanager
    def _op_log(self, event: str, *, device: str = "", boundary: str = "active",
                transport: str | None = None, start: bool = False, **detail):
        """Wrap a hardware operation so the event log records its outcome.

        Emits `outcome="ok"` on normal exit and `outcome="error"` (with the message)
        on exception, then re-raises — the engine is the single point that serialises
        every radio op, so it is the natural emission point. `start=True` also emits an
        `outcome="start"` on entry, so the live tail shows a long op the moment it begins.
        """
        tr = transport if transport is not None else self._kind
        if start:
            self._events.emit(event, device=device, transport=tr, boundary=boundary,
                              outcome="start", **detail)
        try:
            yield
        except Exception as err:                                # noqa: BLE001
            self._events.emit(event, device=device, transport=tr, boundary=boundary,
                              outcome="error", error=str(err), **detail)
            raise
        else:
            self._events.emit(event, device=device, transport=tr, boundary=boundary,
                              outcome="ok", **detail)

    def query_events(self, event: str = "", device: str = "", action: str = "",
                     limit: int = 200) -> list[dict]:
        """Filtered event history (read-only): by exact `event`, `device`, or `action`
        substring, newest first. Reused by the webui Logs page and the MCP tool."""
        return self._events.read(event=event or None, device=device or None,
                                 action=action or None, limit=limit)

    def tail_events(self, n: int = 8) -> list[dict]:
        """The most recent `n` events, oldest-first (for the always-on log footer)."""
        return self._events.tail(n)

    # --- scan lifecycle ----------------------------------------------------
    def start_scan(self, kind: str) -> dict:
        """Open the chosen backend and begin scanning. One scan at a time."""
        if kind not in _KINDS:
            raise ValueError(f"unknown scan kind: {kind!r} (expected one of {list(_KINDS)})")
        with self._op_log("scan_start", action=kind, transport=kind), self._lock:
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
        with self._op_log("scan_stop", action=self._kind or ""), self._lock:
            if self._scanner is None:
                return self.status()
            self._gatt_teardown()               # close any held-open GATT session first
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
            # Watchdog: auto-close a GATT session left idle (tab closed mid-session),
            # which would otherwise pin the scan paused and leak the LE link.
            if self._gatt_peer is not None and time.time() > self._gatt_deadline:
                try:
                    self.gatt_disconnect(self._gatt_addr or "")
                except Exception as err:                        # noqa: BLE001
                    self._last_error = f"gatt idle-close: {err}"

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
        with self._op_log("browse_gatt", device=address, action="one-shot", start=True):
            with self._lock:
                if self._scanner is None:
                    raise RuntimeError("no scan running — start a BLE scan first")
                if self._kind not in _BLE_KINDS:
                    raise RuntimeError("GATT browse needs the BLE dongle (start a 'ble' or 'fused' scan)")
                self._gatt_teardown()   # a one-shot browse supersedes any held-open session
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

    # --- held-open GATT interaction session (Task 1) -----------------------
    def gatt_session(self) -> dict:
        """Current interaction-session state (for the UI/MCP): which device, which
        characteristics are subscribed. `open` means a peer is held connected."""
        return {
            "address": self._gatt_addr,
            "open": self._gatt_peer is not None,
            "connected": bool(self._gatt_peer is not None
                              and getattr(self._gatt_peer, "connected", False)),
            "subscribed": sorted(self._gatt_subscribed),
            "pair_status": self._gatt_pair_status,
            "fp_status": self._gatt_fp_status,
        }

    def gatt_connect(self, address: str) -> dict:
        """Open a held-open interaction session: pause the scan, connect, keep the link.

        Unlike `browse_gatt` (one-shot connect→read→disconnect), this keeps the peer
        connected across later read/write/subscribe requests — the dongle cannot scan
        and connect at once, so the scan stays paused until `gatt_disconnect` (or the
        idle watchdog) closes the session. Persists the discovered tree and backfills
        the GATT Device Name, exactly like `browse_gatt`.
        """
        address = store_module.normalize_address(address)
        with self._op_log("gatt_connect", device=address, action="session open", start=True):
            with self._lock:
                if self._scanner is None:
                    raise RuntimeError("no scan running — start a BLE scan first")
                if self._kind not in _BLE_KINDS:
                    raise RuntimeError("GATT interaction needs the BLE dongle "
                                       "(start a 'ble' or 'fused' scan)")
                if self._gatt_peer is not None:
                    if self._gatt_addr == address:
                        self._touch_session()           # already open on this device
                        return self.gatt_session()
                    self._gatt_teardown()                # a session for another device
                ble_device, peer = self._dongle_and_peer(address)
                if peer is None:
                    raise RuntimeError(f"{address} not currently seen by the dongle "
                                       "(it may have rotated its address or moved out of range)")
                self._scanner.pause()
                conn = None
                try:
                    conn = self._connect(ble_device, peer)
                    self._guard_services(conn)
                    result = self._tree_from_peer(conn)
                except Exception:
                    if conn is not None:
                        try:
                            conn.disconnect().wait(5, exception_on_timeout=False)
                        except Exception:
                            pass
                    self._scanner.resume()               # no session to hold it paused
                    raise
                self._gatt_addr = address
                self._gatt_peer = conn
                self._gatt_notifs = {}
                self._gatt_subscribed = set()
                self._touch_session()
            self._store.save_gatt(address, result["gatt"], self._session_id)
            if result.get("device_name"):
                self._store.set_name(address, result["device_name"])
            return self.gatt_session()

    def gatt_read(self, address: str, char_uuid: str) -> dict:
        """Read one characteristic on the open session; persist the fresh value."""
        address = store_module.normalize_address(address)
        with self._op_log("gatt_read", device=address, action=char_uuid):
            with self._lock:
                conn = self._require_session(address)
                ch = self._find_char(conn, char_uuid)
                if ch is None:
                    raise RuntimeError(f"characteristic {char_uuid} not found in the session")
                if not ch.readable:
                    raise RuntimeError("characteristic is not readable")
                try:
                    _c, ev = ch.read().wait(5, exception_on_timeout=False)
                except Exception as err:                        # noqa: BLE001
                    raise RuntimeError(f"read failed: {err}")
                if ev is None or getattr(ev, "value", None) is None:
                    raise RuntimeError("read failed/timed out")
                val_hex = ev.value.hex()
                interp = client._interpret(ch.uuid, ev.value) or ""
                self._touch_session()
            self._store.update_char_value(address, char_uuid, val_hex, interp)
            return {"uuid": char_uuid, "value_hex": val_hex, "value_interp": interp}

    def gatt_write(self, address: str, char_uuid: str, value: str, mode: str = "auto") -> dict:
        """ACTIVE: write a value to one characteristic on the open session.

        `mode` is hex | text | auto; the value is parsed with `client._parse_write_value`
        so the web/MCP write path matches the CLI exactly.
        """
        address = store_module.normalize_address(address)
        with self._op_log("gatt_write", device=address, action=char_uuid):
            with self._lock:
                conn = self._require_session(address)
                ch = self._find_char(conn, char_uuid)
                if ch is None:
                    raise RuntimeError(f"characteristic {char_uuid} not found in the session")
                if not (ch.writable or ch.writable_without_response):
                    raise RuntimeError("characteristic is not writable")
                try:
                    data = self._parse_write(value, mode)
                except ValueError:
                    raise RuntimeError("invalid hex value")
                ok = client.write_char_value(ch, data)
                self._touch_session()
            if not ok:
                raise RuntimeError("write failed/timed out")
            return {"uuid": char_uuid, "wrote_hex": data.hex(), "bytes": len(data)}

    def gatt_subscribe(self, address: str, char_uuid: str) -> dict:
        """Subscribe to notifications on one characteristic; they buffer for polling."""
        address = store_module.normalize_address(address)
        with self._op_log("gatt_subscribe", device=address, action=char_uuid):
            with self._lock:
                conn = self._require_session(address)
                ch = self._find_char(conn, char_uuid)
                if ch is None:
                    raise RuntimeError(f"characteristic {char_uuid} not found in the session")
                if not ch.subscribable:
                    raise RuntimeError("characteristic is not notifiable")
                key = str(ch.uuid).lower()
                if key not in self._gatt_subscribed:
                    self._gatt_notifs.setdefault(key, [])

                    def _on_notify(c, e, _key=key):
                        # Runs on blatann's event thread. `list.append` is atomic under
                        # the GIL, and the poller swaps the list rather than mutating it.
                        val = getattr(e, "value", None)
                        if val is None:
                            return
                        self._gatt_notifs.setdefault(_key, []).append({
                            "ts": time.time(), "hex": val.hex(),
                            "interp": client._interpret(c.uuid, val) or "",
                        })

                    ch.subscribe(_on_notify).wait(5, exception_on_timeout=False)
                    self._gatt_subscribed.add(key)
                self._touch_session()
            return {"uuid": char_uuid, "subscribed": True}

    def gatt_unsubscribe(self, address: str, char_uuid: str) -> dict:
        address = store_module.normalize_address(address)
        with self._op_log("gatt_unsubscribe", device=address, action=char_uuid):
            with self._lock:
                conn = self._require_session(address)
                ch = self._find_char(conn, char_uuid)
                key = str(ch.uuid).lower() if ch is not None else char_uuid.lower()
                if ch is not None and key in self._gatt_subscribed:
                    try:
                        ch.unsubscribe()
                    except Exception:                           # noqa: BLE001
                        pass
                self._gatt_subscribed.discard(key)
                self._touch_session()
            return {"uuid": char_uuid, "subscribed": False}

    def gatt_notifications(self, address: str) -> dict:
        """Drain buffered notifications for the open session (consumed on read).

        Returns only notifications arrived since the last call, so the page can append
        them to a log via HTMX `hx-swap="beforeend"` polling.
        """
        address = store_module.normalize_address(address)
        with self._lock:
            if self._gatt_peer is None or self._gatt_addr != address:
                return {"open": False, "subscribed": [], "notifications": []}
            out = []
            for key in sorted(self._gatt_subscribed):
                buf = self._gatt_notifs.get(key, [])
                self._gatt_notifs[key] = []                     # swap: new appends go here
                out.extend({"uuid": key, **n} for n in buf)
            out.sort(key=lambda n: n["ts"])
            self._touch_session()
            return {"open": True, "subscribed": sorted(self._gatt_subscribed),
                    "notifications": out}

    def gatt_disconnect(self, address: str = "") -> dict:
        """Close the held-open session: unsubscribe, disconnect, resume the scan."""
        with self._op_log("gatt_disconnect", device=address, action="session close"):
            with self._lock:
                self._gatt_teardown()
            return self.gatt_session()

    def gatt_pair(self, address: str, method: str = "dongle") -> dict:
        """ACTIVE: attempt to bond with `address` over one of two physically distinct radios.

        `method="dongle"` pairs central-side on the peer held by an **open interaction
        session** (`_gatt_peer`), reusing that link rather than reconnecting — the gatt_*
        session model. Just Works by default (bond, no passkey, IO NONE), mirroring the
        proven `emulate.py --positive-control` params but on the central's `peer.security`.
        `method="bluez"` drives `bluetoothctl pair`/`trust` over the host adapter (hci0),
        independent of the dongle; the device must already be in BlueZ's cache (true during
        a *fused* scan). The two backends run on separate radios, so they don't contend.

        The outcome is reported faithfully — a refused bond (hardened Fast Pair accessories
        reject Just Works, cf. NOTES 2026-08-28) is a valid finding, not an error. Returns
        ``{"method", "status", "bonded", "detail"}`` and stashes it for the UI/MCP.
        """
        address = store_module.normalize_address(address)
        with self._op_log("gatt_pair", device=address, action=method,
                          boundary="active", start=True):
            if method == "dongle":
                result = self._pair_dongle(address)
            elif method == "bluez":
                # No dongle/session involved; don't hold the lock across the ~50 s
                # bluetoothctl subprocess (it would freeze the flush thread / other ops).
                out = scan_classic.pair(address).strip()
                low = out.lower()
                result = {"method": "bluez", "status": out,
                          "bonded": "paired and trusted" in low, "detail": out}
            else:
                raise ValueError(f"unknown pair method {method!r} (use 'dongle' or 'bluez')")
            self._gatt_pair_status = result
            return result

    def _pair_dongle(self, address: str) -> dict:
        """Central-side Just Works pairing on the open session's peer. Lock + session held."""
        from blatann.gap import IoCapabilities
        with self._lock:
            conn = self._require_session(address)
            conn.security.set_security_params(
                passcode_pairing=False, bond=True, lesc_pairing=False,
                io_capabilities=IoCapabilities.NONE, out_of_band=False)
            try:
                _peer, ev = conn.security.pair().wait(30, exception_on_timeout=False)
            except Exception as err:                                # noqa: BLE001
                raise RuntimeError(f"pairing failed: {err}")
            self._touch_session()
        if ev is None:
            return {"method": "dongle", "status": "timeout", "bonded": False,
                    "detail": "pairing timed out (no response from the peer)"}
        status = getattr(ev.status, "name", str(ev.status))
        level = getattr(ev.security_level, "name", str(ev.security_level))
        bonded = status == "success"
        return {"method": "dongle", "status": status, "bonded": bonded,
                "detail": f"status={status}, security_level={level}"}

    # --- Fast Pair Key-based Pairing (active — TODO items 2 & 3) -----------
    def _fp_kbp_char(self, address: str):
        """The open session's Key-based Pairing characteristic (…1234), or raise. Lock held."""
        conn = self._require_session(address)
        kbp = self._find_char(conn, fastpair.KBP_UUID)
        if kbp is None:
            raise RuntimeError("device exposes no Fast Pair Key-based Pairing characteristic "
                               "(…1234) — not a Fast Pair Provider, or GATT not fully browsed")
        return kbp

    def _fp_write_await_notify(self, kbp, payload: bytes, timeout: float = 10.0) -> bytes | None:
        """Write `payload` to the KBP char and wait for one notify. Returns the raw notify or None.

        Subscribes a one-shot handler around the write (the handshake is request→response,
        not a stream, so it does NOT use the session's polled notification buffer). Lock held.
        """
        got = threading.Event()
        box: dict[str, bytes] = {}

        def _on_notify(_c, e):                                   # blatann event thread
            val = getattr(e, "value", None)
            if val is not None and "raw" not in box:
                box["raw"] = bytes(val)
                got.set()

        subscribed = False
        if kbp.subscribable:
            kbp.subscribe(_on_notify).wait(5, exception_on_timeout=False)
            subscribed = True
        try:
            if not client.write_char_value(kbp, payload):
                raise RuntimeError("write to Key-based Pairing failed/timed out")
            if subscribed:
                got.wait(timeout)
        finally:
            if subscribed:
                try:
                    kbp.unsubscribe()
                except Exception:                               # noqa: BLE001
                    pass
        return box.get("raw")

    def fastpair_resolve_key(self, model_id: str, allow_network: bool = False) -> dict:
        """Resolve a Fast Pair anti-spoofing PUBLIC key by Model ID (file → opt-in Google).

        Offline file lookup is a passive read (not logged); an opt-in Google fetch is a
        **network** op (logged as such). Returns `{model_id, public_key_hex|None, source,
        detail}`. See `fastpair_keys.resolve`."""
        if not allow_network:
            r = fastpair_keys.resolve(model_id, allow_network=False)
            return {"model_id": r["model_id"], "source": r["source"], "detail": r["detail"],
                    "public_key_hex": r["public_key"].hex() if r["public_key"] else None}
        with self._op_log("fastpair_key_fetch", action=f"model {model_id}",
                          boundary="network", start=True):
            r = fastpair_keys.resolve(model_id, allow_network=True)
            return {"model_id": r["model_id"], "source": r["source"], "detail": r["detail"],
                    "public_key_hex": r["public_key"].hex() if r["public_key"] else None}

    def _fp_acquire_pubkey(self, provider_public_hex: str, model_id: str,
                           allow_network: bool) -> bytes:
        """Resolve the Provider public key for a handshake: explicit hex wins, else Model ID."""
        if provider_public_hex and provider_public_hex.strip():
            key = fastpair_keys._decode_key_text(provider_public_hex)
            if key is None:
                raise RuntimeError("provider public key must be 64 bytes (128 hex / base64)")
            return key
        if model_id and model_id.strip():
            r = fastpair_keys.resolve(model_id, allow_network=allow_network)
            if r["public_key"] is None:
                raise RuntimeError(f"could not resolve a public key for model {model_id!r}: "
                                   f"{r['detail']}")
            return r["public_key"]
        raise RuntimeError("provide the Provider's public key (hex/base64) or a Model ID")

    def fastpair_handshake(self, address: str, provider_public_hex: str = "",
                           model_id: str = "", allow_network: bool = False) -> dict:
        """ACTIVE: run the Fast Pair Key-based Pairing handshake on the open session.

        Writes an AES-encrypted Key-based Pairing Request (Case 1, with our ephemeral
        public key appended) to …1234 and waits for the Provider's notified response,
        then decrypts it. The Provider's 64-byte anti-spoofing public key comes either
        from `provider_public_hex` (hex/base64 — e.g. what `emulate.py --fast-pair-gatt`
        prints for the positive control) or, if omitted, resolved by `model_id` via
        `fastpair_keys` (local file, plus an opt-in Google lookup when `allow_network`).

        The outcome is reported faithfully: a response that does not decrypt as a valid
        type-0x01 block, or no notify at all, is a *finding* (the Provider rejected or
        ignored us), not an exception. Returns a dict stashed on `gatt_session().fp_status`.
        """
        address = store_module.normalize_address(address)
        with self._op_log("fastpair_handshake", device=address,
                          action="key-based pairing", boundary="active", start=True):
            provider_pub = self._fp_acquire_pubkey(provider_public_hex, model_id, allow_network)
            seeker = fastpair.gen_keypair()
            key = fastpair.derive_key(seeker, provider_pub)
            request = fastpair.build_kbp_request(
                key, address, public_key64=fastpair.public_key_bytes(seeker))
            with self._lock:
                kbp = self._fp_kbp_char(address)
                raw = self._fp_write_await_notify(kbp, request)
                self._touch_session()
            result = self._fp_interpret_response(key, raw, request)
            self._gatt_fp_status = result
            return result

    @staticmethod
    def _fp_interpret_response(key: bytes, raw: bytes | None, request: bytes) -> dict:
        base = {"action": "handshake", "request_hex": request.hex()}
        if raw is None:
            return {**base, "ok": False,
                    "detail": "no notify from Key-based Pairing within timeout — "
                              "the Provider ignored the request"}
        try:
            resp = fastpair.parse_kbp_response(key, raw)
        except ValueError as err:
            return {**base, "ok": False, "notified_hex": raw.hex(),
                    "detail": f"response did not decrypt as a valid KBP response: {err}"}
        return {**base, "ok": True, "notified_hex": raw.hex(),
                "response_type": resp["type"], "provider_addr": resp["provider_addr"],
                "salt": resp["salt"].hex(),
                "detail": f"valid Key-based Pairing Response — Provider address "
                          f"{resp['provider_addr']}"}

    # Stress payloads for …1234: (name, bytes-or-builder). Builders take the KBP char's
    # device address so a "valid-type-wrong-key" case still targets the right Provider.
    _FP_STRESS_VARIANTS = {
        "unencrypted-zero": "16 zero bytes (unencrypted, type 0x00) — no valid ciphertext",
        "wrong-type": "16 bytes of a block whose plaintext type is 0xFF",
        "too-short": "a 4-byte write (below the 16-byte block size)",
        "too-long": "a 100-byte write (above request+pubkey)",
        "random-16": "16 random bytes (garbage ciphertext)",
    }

    def fastpair_stress(self, address: str, variant: str) -> dict:
        """ACTIVE: write a malformed Key-based Pairing payload and report the Provider's reaction.

        Exercises how a Provider handles invalid input on the crypto-gated-but-GATT-
        unencrypted …1234 (authorized robustness testing). Reports the notify (if any),
        the write outcome, and whether the link dropped — all valid findings.
        """
        address = store_module.normalize_address(address)
        if variant not in self._FP_STRESS_VARIANTS:
            raise ValueError(f"unknown stress variant {variant!r} "
                             f"(use one of {', '.join(self._FP_STRESS_VARIANTS)})")
        with self._op_log("fastpair_stress", device=address,
                          action=variant, boundary="active", start=True):
            if variant == "unencrypted-zero":
                payload = bytes(16)
            elif variant == "wrong-type":
                # A deterministic 16-byte block; its plaintext is whatever, the point is
                # the Provider must reject it (no matching account/anti-spoofing key).
                payload = bytes([0xFF]) + bytes(15)
            elif variant == "too-short":
                payload = bytes(4)
            elif variant == "too-long":
                payload = bytes(100)
            else:                                               # random-16
                payload = os.urandom(16)
            with self._lock:
                kbp = self._fp_kbp_char(address)
                raw = self._fp_write_await_notify(kbp, payload, timeout=5.0)
                conn = self._gatt_peer
                still_connected = bool(getattr(conn, "connected", False))
                self._touch_session()
            result = {
                "action": "stress", "variant": variant,
                "description": self._FP_STRESS_VARIANTS[variant],
                "sent_hex": payload.hex(),
                "notified_hex": raw.hex() if raw is not None else "",
                "notify": raw is not None,
                "still_connected": still_connected,
                "detail": ("Provider notified a response" if raw is not None
                           else "Provider sent no notify")
                          + ("" if still_connected else " — connection dropped"),
            }
            self._gatt_fp_status = result
            return result

    # --- session helpers ---------------------------------------------------
    def _require_session(self, address: str):
        """The connected peer for an open session on `address`, or raise. Lock held."""
        address = store_module.normalize_address(address)
        if self._gatt_peer is None:
            raise RuntimeError("no GATT session open — connect first")
        if self._gatt_addr != address:
            raise RuntimeError(f"the open GATT session is for {self._gatt_addr}, not {address}")
        if not getattr(self._gatt_peer, "connected", False):
            raise RuntimeError("the GATT session's connection dropped — close and reconnect")
        return self._gatt_peer

    def _touch_session(self) -> None:
        self._gatt_deadline = time.time() + self.GATT_SESSION_IDLE

    @staticmethod
    def _parse_write(value: str, mode: str) -> bytes:
        if mode == "hex":
            return client._parse_write_value("hex:" + value)
        if mode == "text":
            return client._parse_write_value("text:" + value)
        return client._parse_write_value(value)

    @staticmethod
    def _find_char(conn, char_uuid: str):
        target = str(char_uuid).lower()
        for svc in conn.database.services:
            for ch in svc.characteristics:
                if str(ch.uuid).lower() == target:
                    return ch
        return None

    def _gatt_teardown(self) -> None:
        """Tear down a held-open session and resume the scan it paused. Lock held."""
        if self._gatt_peer is None:
            return
        peer = self._gatt_peer
        for key in list(self._gatt_subscribed):
            ch = self._find_char(peer, key)
            if ch is not None:
                try:
                    ch.unsubscribe()
                except Exception:                               # noqa: BLE001
                    pass
        try:
            peer.disconnect().wait(5, exception_on_timeout=False)
        except Exception:                                       # noqa: BLE001
            pass
        self._gatt_peer = None
        self._gatt_addr = None
        self._gatt_notifs = {}
        self._gatt_subscribed = set()
        self._gatt_pair_status = None
        self._gatt_fp_status = None
        if self._scanner is not None:
            try:
                self._scanner.resume()
            except Exception:                                   # noqa: BLE001
                pass

    def browse_gatt_bluez(self, address: str) -> dict:
        """Enumerate GATT over the host BlueZ adapter (hci0), independent of the dongle.

        The fallback for services the dongle/blatann could not fully enumerate (vendor
        services whose characteristics trigger the blatann wedge). Runs anytime — it
        does its own connect over hci0 — and merges the result into the stored tree,
        preferring whichever source has more characteristics per service. A fused/classic
        scan is paused for it (it also uses hci0); a dongle-only scan is left untouched.
        """
        address = store_module.normalize_address(address)
        with self._op_log("browse_gatt_bluez", device=address, transport="bluez",
                          action="hci0 enumerate", start=True):
            with self._lock:
                paused = False
                if self._scanner is not None and self._kind in ("fused", "classic"):
                    self._scanner.pause()
                    paused = True
                try:
                    result = gatt_bluez.enumerate(address)
                finally:
                    if paused:
                        self._scanner.resume()
            if result.get("error") and not result.get("gatt"):
                raise RuntimeError(result["error"])
            merged = self._merge_gatt(self._store.get_gatt(address), result["gatt"])
            self._store.save_gatt(address, merged, self._session_id)
            return {"gatt": merged, "error": result.get("error", ""), "source": "bluez"}

    @staticmethod
    def _svc_key(uuid: str) -> str:
        """Normalise a service UUID for cross-source merging (dongle vs BlueZ forms).

        The dongle stores '1800'/full-128, BlueZ '0x1800'/full-128 — reduce both to a
        bare lowercase hex and collapse a SIG base UUID to its 16-bit form.
        """
        u = (uuid or "").lower().replace("0x", "")
        if u.endswith("-0000-1000-8000-00805f9b34fb") and u.startswith("0000"):
            return u[4:8]
        return u

    def _merge_gatt(self, dongle_tree: list[dict], bluez_tree: list[dict]) -> list[dict]:
        """Union two GATT trees by service, keeping the richer characteristic set.

        Per service, the version with more characteristics wins (so BlueZ fills the
        vendor services the dongle left empty, while the dongle keeps the standard
        services whose values it already read). Dongle order first, then BlueZ-only."""
        out: dict[str, dict] = {}
        order: list[str] = []
        for tree in (dongle_tree, bluez_tree):
            for svc in tree:
                k = self._svc_key(svc.get("uuid", ""))
                if k not in out:
                    out[k] = dict(svc)
                    order.append(k)
                    continue
                cur = out[k]
                if len(svc.get("characteristics", [])) > len(cur.get("characteristics", [])):
                    richer = dict(svc)
                    richer["name"] = svc.get("name") or cur.get("name") or ""
                    out[k] = richer
                elif not cur.get("name") and svc.get("name"):
                    cur["name"] = svc["name"]
        return [out[k] for k in order]

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

    @staticmethod
    def _guard_services(conn) -> None:
        """Ensure discovery populated services, else raise a clear, actionable error.

        A dropped link during discovery leaves no services and makes every read time
        out (5 s each). blatann logs the driver-thread cause as NrfError.invalid_state
        on characteristic discovery — detect it here and fail clearly rather than
        persist an empty tree over good data. The blatann workaround
        (`webui.blatann_patch`) finalises discovery with partial results instead of
        wedging, so an empty database is now the genuine "nothing discoverable" case.
        An invalid_state can also be transient, so give discovery one more pass first.
        """
        if not conn.database.services and conn.connected:
            conn.discover_services().wait(10, exception_on_timeout=False)
        if conn.database.services:
            return
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

    def _tree_from_peer(self, conn) -> dict:
        """Build the GATT tree + capability report from a connected, discovered peer.

        Reads every readable characteristic (skipping reads once the link is gone, so
        they don't each block 5 s). Does NOT disconnect — shared by the one-shot
        `browse_gatt` and the held-open interaction session, which owns the teardown.
        """
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
                if ch.readable and conn.connected:
                    try:
                        _c, ev = ch.read().wait(5, exception_on_timeout=False)
                    except Exception:                           # noqa: BLE001
                        ev = None
                    if ev is not None and getattr(ev, "value", None) is not None:
                        val_hex = ev.value.hex()
                        interp = client._interpret(ch.uuid, ev.value) or ""
                        # GATT Device Name (0x2A00) — the only name some devices expose
                        # (they advertise none); backfill it on the record.
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
        # A service that reported zero characteristics is almost always the blatann
        # wedge finalised early by the workaround — flag it so the UI can offer the
        # BlueZ fallback to complete the enumeration.
        incomplete = [n["uuid"] for n in tree if not n["characteristics"]]
        return {"gatt": tree, "capabilities": [lbl for lbl, _note in detected],
                "capability_report": capreport, "device_name": device_name,
                "partial": bool(incomplete), "incomplete_services": incomplete}

    def _discover(self, ble_device, peer) -> dict:
        """Connect, walk the GATT database reading readable chars, disconnect (one-shot)."""
        conn = self._connect(ble_device, peer)
        try:
            self._guard_services(conn)
            return self._tree_from_peer(conn)
        finally:
            # Always tear the link down (not only when conn.connected looks True) —
            # a wedged discovery can leave blatann's view and the SoftDevice link out
            # of sync, and a leaked connection blocks the next browse.
            try:
                conn.disconnect().wait(5, exception_on_timeout=False)
            except Exception:
                pass

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
        # Capability fingerprint from the stored GATT tree (Task 2) — passive, computed
        # from structure already discovered, so the detail page shows it without a live
        # connection and refreshes whenever a browse/session updates the tree.
        d["capabilities"] = self._capabilities_from_tree(d.get("gatt", []))
        d["fast_pair_profile"] = self._fast_pair_profile_from_tree(d.get("gatt", []))
        d["gatt_session"] = self.gatt_session()
        return d

    # --- capability fingerprint (Task 2) -----------------------------------
    @staticmethod
    def _tree_as_objects(tree: list[dict]) -> list:
        """Adapt a stored GATT tree (dicts) to objects `capabilities.*` understands.

        `capabilities` reads `.uuid` plus the boolean char flags; the stored tree keeps
        props as the compact "R/W/N" string, so parse it back into the flags here.
        """
        objs = []
        for s in tree:
            chars = []
            for c in s.get("characteristics", []):
                p = c.get("props", "") or ""
                chars.append(SimpleNamespace(
                    uuid=c.get("uuid", ""),
                    readable="R" in p, writable="W" in p,
                    writable_without_response=False, subscribable="N" in p,
                ))
            objs.append(SimpleNamespace(uuid=s.get("uuid", ""), characteristics=chars))
        return objs

    def _capabilities_from_tree(self, tree: list[dict]) -> dict | None:
        if not tree:
            return None
        services = self._tree_as_objects(tree)
        detected, flags, n_svc, n_char = capabilities.analyze(services)
        dfu = ""
        if capabilities.match_services(services, "Firmware update (DFU/OTA)"):
            dfu = capabilities.dfu_exposure(services)
        return {
            "detected": [{"label": lbl, "note": note} for lbl, note in detected],
            "flags": flags, "n_services": n_svc, "n_characteristics": n_char,
            "report": capabilities.report(services), "dfu_exposure": dfu,
        }

    def device_capabilities(self, address: str) -> dict | None:
        """Capability fingerprint for a device from its stored GATT tree (read-only)."""
        d = self._store.get_device(address)
        if d is None:
            return None
        return self._capabilities_from_tree(d.get("gatt", []))

    # --- Fast Pair profile (passive surfacing — TODO item 1) ----------------
    @staticmethod
    def _fast_pair_profile_from_tree(tree: list[dict]) -> dict | None:
        """Which Fast Pair GATT characteristics a stored tree exposes, and their props.

        Passive: reads the structure already discovered (service ``0xFE2C``), never
        connects. Confirms the device is a real Fast Pair Provider and ties into the
        passive Model-ID enrichment (`bledev/fastpair.py`). Returns ``None`` when no
        FE2C service is present in the tree.
        """
        def _is_fe2c(uuid: str) -> bool:
            # The tree keeps 16-bit UUIDs as the 4-hex short form ("fe2c", like
            # capabilities.py), but tolerate a full base-UUID form too.
            u = str(uuid).lower()
            return u == fastpair.SERVICE_UUID16 or u.startswith("0000" + fastpair.SERVICE_UUID16)

        svc = next((s for s in tree if _is_fe2c(s.get("uuid", ""))), None)
        if svc is None:
            return None
        found = {str(c.get("uuid", "")).lower(): (c.get("props", "") or "")
                 for c in svc.get("characteristics", [])}
        chars = []
        for uuid, name in fastpair.FP_CHARACTERISTICS.items():
            chars.append({
                "uuid": uuid, "name": name,
                "present": uuid in found, "props": found.get(uuid, ""),
            })
        return {
            "service_uuid": str(svc.get("uuid", "")),
            "characteristics": chars,
            "n_present": sum(1 for c in chars if c["present"]),
        }

    def fast_pair_profile(self, address: str) -> dict | None:
        """Fast Pair GATT profile for a device from its stored tree (read-only, passive)."""
        d = self._store.get_device(address)
        if d is None:
            return None
        return self._fast_pair_profile_from_tree(d.get("gatt", []))

    # --- Classic SDP profiles (Task 5) -------------------------------------
    def classic_profiles(self, address: str) -> dict:
        """Read the device's SDP profiles over BlueZ and classify them.

        Active (host-side): queries `bluetoothctl info` via `scan_classic.read_profiles`.
        A running fused/classic scan is paused for it (it also uses hci0), mirroring
        `browse_gatt_bluez`; a dongle-only BLE scan is left untouched.
        """
        address = store_module.normalize_address(address)
        with self._op_log("classic_profiles", device=address, transport="bluez",
                          action="SDP read", start=True):
            with self._lock:
                paused = False
                if self._scanner is not None and self._kind in ("fused", "classic"):
                    self._scanner.pause()
                    paused = True
                try:
                    uuid16s, vendor = scan_classic.read_profiles(address)
                finally:
                    if paused:
                        self._scanner.resume()
            detected = capabilities.classic_detected(uuid16s)
            return {
                "uuid16s": sorted(f"0x{u:04X}" for u in uuid16s),
                "vendor_count": vendor,
                "detected": [{"label": lbl, "note": note} for lbl, note in detected],
                "report": capabilities.classic_report(uuid16s, vendor),
            }

    def exposure_report(self, address: str) -> str | None:
        d = self._store.get_device(address)
        if d is None:
            return None
        rec = store_module.row_to_record(d)
        rec.services = {s["uuid"] for s in d["advertised_services"]}
        return exposure.report(rec)

    def list_sessions(self) -> list[dict]:
        return self._store.list_sessions()

    def current_session_id(self) -> int | None:
        """The scan session to scope the "current/last scan" device view to: the live
        one while scanning, otherwise the most recent session on record (or None if the
        store has never seen a scan)."""
        if self._session_id is not None:
            return self._session_id
        sessions = self._store.list_sessions()          # newest first
        return sessions[0]["id"] if sessions else None

    def prune_devices(self, before: float) -> int:
        """Delete persisted devices older than ``before`` (epoch). Destructive — the
        caller gates it behind an explicit, confirmed action."""
        return self._store.prune_devices(before)


# Module-level singleton: the one hardware owner for this process.
_engine: Engine | None = None
_engine_lock = threading.Lock()


def get_engine() -> Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = Engine()
        return _engine
