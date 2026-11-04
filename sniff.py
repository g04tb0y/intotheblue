"""Live BLE sniff via the CC2652P (Sonoff ZBDongle-P) running Sniffle firmware.

This is a **third, independent scan backend** next to the BLE side (nRF52/blatann,
`scan_live.py`) and the Classic side (BlueZ, `scan_classic.py`). It drives the Sonoff
dongle over its USB serial bridge through the vendored, in-repo `sniffle/` package — it
is **fully independent of blatann** (needs only pyserial), exactly like `scan_classic`
is independent of the dongle. The nRF52 and the Sonoff are *separate* serial devices, so
this backend never contends with the blatann dongle lock.

Why a sniffer at all when the nRF52 already scans: a true BLE sniffer sees traffic the
host's own controller never surfaces — most importantly it can **follow a connection**
(observe a `CONNECT_IND` and the followed data channel), which exposes a phone↔device
relationship directly. That is the heart of the standing project goal (attribute paired
devices). Two modes share this one backend:

- **Phase 1 — advertising scan** (``follow=False``): per-address advertising records, the
  Sniffle equivalent of `scan_live`. Proves the backend in the scan→select→act UI.
- **Phase 2 — connection following** (``follow=True``): `SnifferMode.CONN_FOLLOW` captures
  each `CONNECT_IND` as a **connection record** naming both peers (central `InitA`,
  typically the phone, ↔ peripheral `AdvA`, the device) plus the link parameters, and
  counts the followed data-channel traffic. This is the first surface that shows a
  phone↔device pairing directly. The device name is enriched from the device's *own*
  advertising (kept alongside), not from the connection: modern links encrypt before GATT,
  so the ATT payloads are ciphertext — the ``encrypted`` flag records exactly that, and
  proper ATT decode is left to the Phase-3 PCAP export (Wireshark).

Boundary: both modes are **receive-only** — Sniffle never transmits in sniffer mode (not
even in CONN_FOLLOW; it only listens to an existing connection). The one transmitting
option is `ACTIVE_SCAN` (opt-in via ``active=True``), which sends `SCAN_REQ` to elicit scan
responses (names); it is labelled as such. Following needs a *fresh* `CONNECT_IND`, so the
operator must start the sniffer and then trigger a reconnection (toggle BT / power-cycle
the device).

Sniffle's packet decoder output format is firmware-version-sensitive; re-validate against
hardware after any re-vendor of `sniffle/` (see `sniffle/VENDORED.md`).

Usage:
    python sniff.py [--port /dev/ttyUSB0] [--active]
    python sniff.py --follow [--target AA:BB:CC:DD:EE:FF | --irk HEX]
"""
from __future__ import annotations

import argparse
import csv
import glob
import logging
import os
import threading
import time
from dataclasses import dataclass, field

import yaml

import livetable
from sniffle.constants import PhyMode, SnifferMode
from sniffle.packet_decoder import (
    AdvaMessage,
    AdvDirectIndMessage,
    AdvIndMessage,
    AdvNonconnIndMessage,
    AdvScanIndMessage,
    ConnectIndMessage,
    DataMessage,
    LlControlMessage,
    ScanRspMessage,
    str_mac,
)
from sniffle.pcap import PcapBleWriter
from sniffle.sniffle_hw import SniffleHW

# Stable by-id link for the Sonoff bridge (CP210x), independent of enumeration order.
_PORT_GLOB = "/dev/serial/by-id/*ITead_Sonoff*"
_FALLBACK_PORT = "/dev/ttyUSB0"

# Primary advertising PHY names, indexed by PacketMessage.phy (0..3).
_PHY_NAMES = ("1M", "2M", "Coded", "Coded")

# Compact labels for the BLE address type, matching scan_live's vocabulary so the merged
# store rows read consistently across backends.
_ADDR_TYPE_SHORT = {
    "public": "pub",
    "static": "rnd-s",
    "res": "rnd-r",
    "nonres": "rnd-n",
}

# Connectable legacy PDU types (an undirected or directed connectable advertisement).
_CONNECTABLE = (AdvIndMessage, AdvDirectIndMessage)

# --- AD decoding tables -----------------------------------------------------
# Copied (blatann-free) from scan_live.py so this independent backend shares the same
# vendor/protocol vocabulary without importing blatann. If the duplication grows, factor
# a shared blatann-free decoding helper (the lean choice for now is a local copy).

_SERVICE_DATA_UUIDS = {
    0xFE95: "Xiaomi",
    0xFCF1: "Google",
    0xFE2C: "Google FastPair",
    0xFEAA: "Eddystone",
    0xFD6F: "ExposureNotif",
    0xFEED: "Tile",
    0xFE07: "Sonos",
    0xFDCD: "Qingping",
    0xFE9F: "Google",
    0xFE50: "Google",
    0xFEF3: "Google",
}

_APPLE_MSG_TYPES = {
    0x02: "iBeacon",
    0x05: "AirDrop",
    0x07: "AirPods",
    0x09: "AirPlay",
    0x0A: "AirPrint",
    0x0C: "Handoff",
    0x10: "Nearby",
    0x12: "FindMy",
}

_FAST_PAIR_UUID = 0xFE2C

# Upper bound on raw packets buffered per followed connection for PCAP export. A followed
# link can run indefinitely; capping keeps memory finite while still capturing the start of
# the connection (the dissectable ATT/GATT exchange), which is what the export is for.
_CONN_PCAP_CAP = 50000

# --- Manufacturer (BLE Company ID) resolution -------------------------------
# A small, blatann-free loader of the bundled manufact.yaml snapshot. bledev.manufacturers
# prefers the SIG checkout, but importing it pulls bledev -> blatann, which this backend
# must not depend on; the yaml snapshot is the documented fallback and suffices here.
_MANUF_YAML = os.path.join(os.path.dirname(__file__), "manufact.yaml")
_manuf_lookup: dict[int, str] | None = None


def _manufacturer(mnf_id: int) -> str:
    """'<id> <name>' for a BLE Company Identifier, or 'N/A' if unknown."""
    global _manuf_lookup
    if _manuf_lookup is None:
        try:
            with open(_MANUF_YAML) as fh:
                data = yaml.safe_load(fh)
            _manuf_lookup = {e["value"]: e["name"] for e in data["company_identifiers"]}
        except (OSError, KeyError, TypeError):
            _manuf_lookup = {}
    name = _manuf_lookup.get(mnf_id)
    return f"{mnf_id} {name}" if name else "N/A"


def find_sonoff_port() -> str:
    """Locate the Sonoff/Sniffle dongle's serial port.

    Prefers the stable /dev/serial/by-id link for the ITead Sonoff bridge (resolved to
    the real /dev/ttyUSB*), falling back to /dev/ttyUSB0 — mirroring how
    bledev.find_dongle_port locates the nRF52.
    """
    matches = glob.glob(_PORT_GLOB)
    if matches:
        return os.path.realpath(sorted(matches)[0])
    return _FALLBACK_PORT


# --- Advertising-data (LTV) parsing -----------------------------------------

@dataclass
class _AdvFields:
    """Parsed advertising payload. Sniffle hands us raw `adv_data` bytes (unlike
    blatann's pre-parsed object), so we walk the length-type-value structures ourselves."""
    local_name: str = ""
    flags: int | None = None
    tx_power: int | None = None
    manufacturer_data: bytes = b""
    service_data16: dict[int, bytes] = field(default_factory=dict)
    service_uuids16: set[int] = field(default_factory=set)


def _parse_adv_data(data: bytes) -> _AdvFields:
    """Parse an advertising/scan-response payload into the fields we surface.

    Each AD structure is `len, type, value[len-1]`. A malformed/truncated tail is
    ignored rather than raised — a sniffer sees partial and corrupt frames routinely.
    """
    f = _AdvFields()
    i = 0
    n = len(data)
    while i < n:
        length = data[i]
        if length == 0 or i + 1 + length > n:
            break
        ad_type = data[i + 1]
        value = data[i + 2 : i + 1 + length]
        if ad_type in (0x08, 0x09):  # shortened / complete local name
            if not f.local_name:
                f.local_name = value.decode("utf-8", "replace")
        elif ad_type == 0x01:  # flags
            f.flags = value[0] if value else None
        elif ad_type == 0x0A:  # tx power level (signed int8 dBm)
            if value:
                b = value[0]
                f.tx_power = b - 256 if b >= 128 else b
        elif ad_type == 0xFF:  # manufacturer specific data
            f.manufacturer_data = bytes(value)
        elif ad_type == 0x16:  # service data - 16-bit UUID
            if len(value) >= 2:
                f.service_data16[int.from_bytes(value[:2], "little")] = bytes(value[2:])
        elif ad_type in (0x02, 0x03):  # incomplete / complete 16-bit service UUIDs
            for j in range(0, len(value) - 1, 2):
                f.service_uuids16.add(int.from_bytes(value[j : j + 2], "little"))
        i += 1 + length
    return f


def _decode_protocol(f: _AdvFields) -> str:
    """Human-readable protocol/product label from manufacturer or service data."""
    md = f.manufacturer_data
    if md and len(md) >= 2:
        cid = int.from_bytes(md[:2], "little")
        if cid == 0x004C and len(md) >= 3:  # Apple
            return "Apple " + _APPLE_MSG_TYPES.get(md[2], f"0x{md[2]:02X}")
        if cid == 0x0006:  # Microsoft
            return "MS CDP/SwiftPair"
        if cid == 0x0075:  # Samsung
            return "Samsung"
    for uuid16 in sorted(f.service_data16):
        known = _SERVICE_DATA_UUIDS.get(uuid16)
        return known or f"svc 0x{uuid16:04X}"
    return ""


def _fast_pair(f: _AdvFields) -> str:
    """Fast Pair identification (UUID 0xFE2C): the 3-byte model id (discoverable),
    'Yes' (non-discoverable), or '' if not Fast Pair. Identification only — no interaction."""
    payload = f.service_data16.get(_FAST_PAIR_UUID)
    if payload is not None:
        return payload.hex().upper() if len(payload) == 3 else "Yes"
    if _FAST_PAIR_UUID in f.service_uuids16:
        return "Yes"
    return ""


def _addr_type_short(adva: bytes, tx_add: int) -> str:
    """Compact address-type label from the AdvA bytes and the TxAdd bit.

    TxAdd==0 is a public address. For a random address the top two bits of the MSB
    classify it: 0b00 non-resolvable, 0b01 resolvable, 0b11 static (0b10 is reserved).
    """
    if not tx_add:
        return "pub"
    top = adva[5] >> 6
    return {0b00: "rnd-n", 0b01: "rnd-r", 0b11: "rnd-s"}.get(top, "rnd?")


# --- Data model -------------------------------------------------------------

@dataclass
class SniffleRecord:
    """Per-address advertising record. Attribute names match what store.record_fields
    reads (address, addr_type, name, rssi, connectable, ...) so Sniffle sightings merge
    into the same device rows as the nRF52/BlueZ backends once the engine persists them."""
    address: str
    addr_type: str = "?"
    name: str = ""
    rssi: int = 0
    chan: int = 0
    phy: int = 0
    connectable: bool = False
    directed: bool = False
    manufacturer: str = ""
    protocol: str = ""
    fast_pair: str = ""
    fp_payload: bytes = b""
    tx_power: int | None = None
    flags: int | None = None
    services: set = field(default_factory=set)
    count: int = 0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    def update(self, msg: AdvaMessage) -> None:
        """Fold one decoded advertising/scan-response message into the record."""
        self.last_seen = time.time()
        self.count += 1
        self.rssi = msg.rssi
        self.chan = msg.chan
        self.phy = msg.phy
        self.addr_type = _addr_type_short(msg.AdvA, msg.TxAdd)

        if isinstance(msg, _CONNECTABLE):
            self.connectable = True
        if isinstance(msg, AdvDirectIndMessage):
            self.directed = True  # sticky: reconnect-directed frame to a bonded peer

        # ADV_DIRECT_IND carries no adv_data; the others do.
        fields = _parse_adv_data(bytes(getattr(msg, "adv_data", b"")))
        if fields.local_name and fields.local_name != self.address:
            self.name = fields.local_name
        if fields.manufacturer_data and len(fields.manufacturer_data) >= 2:
            cid = int.from_bytes(fields.manufacturer_data[:2], "little")
            self.manufacturer = _manufacturer(cid)
        label = _decode_protocol(fields)
        if label:
            self.protocol = label
        fp = _fast_pair(fields)
        if fp:
            self.fast_pair = fp
        if _FAST_PAIR_UUID in fields.service_data16:
            self.fp_payload = fields.service_data16[_FAST_PAIR_UUID]
        self.services.update(f"0x{u:04X}" for u in fields.service_uuids16)
        if fields.flags is not None:
            self.flags = fields.flags
        if fields.tx_power is not None:
            self.tx_power = fields.tx_power


# --- Columns ----------------------------------------------------------------
COLUMNS: list[livetable.Column] = [
    ("Address",       19, lambda d: d.address),
    ("Addr",           6, lambda d: d.addr_type),
    ("Name",          16, lambda d: d.name),
    ("RSSI",           5, lambda d: str(d.rssi)),
    ("Ch",             3, lambda d: str(d.chan)),
    ("PHY",            6, lambda d: _PHY_NAMES[d.phy] if d.phy < len(_PHY_NAMES) else "?"),
    ("Conn",           4, lambda d: "Yes" if d.connectable else "-"),
    ("FastPair",      10, lambda d: d.fast_pair or "-"),
    ("Manufacturer",  18, lambda d: d.manufacturer),
    ("Type",          17, lambda d: d.protocol),
    ("Svc",            4, lambda d: str(len(d.services)) if d.services else "-"),
    ("Pkt",            5, lambda d: str(d.count)),
    ("Age",            4, lambda d: f"{int(time.time() - d.last_seen)}s"),
]


# --- Connection-follow data model (Phase 2) ---------------------------------

@dataclass
class ConnectionRecord:
    """One followed BLE connection, keyed by its access address.

    The central is the initiator of the `CONNECT_IND` (`InitA`) — typically the phone;
    the peripheral is the advertiser (`AdvA`) — the device. Holding both on one record is
    the point of connection following: it is the phone↔device relationship made explicit.
    `peripheral_name` is borrowed from the device's own advertising (see module docstring),
    never from the (usually encrypted) connection payload.
    """
    aa: int                       # access address of the followed connection (the key)
    central: str                  # InitA — initiator MAC (the phone, usually)
    central_type: str
    peripheral: str               # AdvA — advertiser MAC (the device)
    peripheral_type: str
    peripheral_name: str = ""     # enriched from the device's advertising, if seen
    rssi: int = 0
    interval_ms: float = 0.0      # connection interval (CONNECT_IND Interval × 1.25 ms)
    latency: int = 0              # peripheral latency (skipped connection events)
    timeout_ms: float = 0.0       # supervision timeout (Timeout × 10 ms)
    hop: int = 0                  # hop increment
    channels: int = 0             # number of used data channels in the channel map
    pkt_central: int = 0          # C→P data-channel packets (data_dir == 0)
    pkt_peripheral: int = 0       # P→C data-channel packets (data_dir == 1)
    encrypted: bool = False       # saw LL_ENC_REQ / LL_START_ENC_REQ on the link
    terminated: bool = False      # saw LL_TERMINATE_IND
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    # Raw decoded packets of this connection (CONNECT_IND + followed data), for PCAP export.
    # Capped at _CONN_PCAP_CAP; the decoded message objects carry everything PcapBleWriter
    # needs (ts_epoch, aa, chan, rssi, body, phy, crc_rev, crc_err).
    packets: list = field(default_factory=list, repr=False)


# Live-table columns for connection-follow mode. The two MAC columns are the deliverable:
# central (phone) on the left, peripheral (device) on the right.
CONN_COLUMNS: list[livetable.Column] = [
    ("Central (phone)", 19, lambda c: c.central),
    ("CT",              4, lambda c: c.central_type),
    ("Peripheral (dev)", 19, lambda c: c.peripheral),
    ("PT",              4, lambda c: c.peripheral_type),
    ("Name",           16, lambda c: c.peripheral_name),
    ("RSSI",            5, lambda c: str(c.rssi)),
    ("Intv",            7, lambda c: f"{c.interval_ms:.0f}ms"),
    ("Enc",             4, lambda c: "Yes" if c.encrypted else "-"),
    ("C>P",             6, lambda c: str(c.pkt_central)),
    ("P>C",             6, lambda c: str(c.pkt_peripheral)),
    ("State",           5, lambda c: "term" if c.terminated else "live"),
    ("Age",             4, lambda c: f"{int(time.time() - c.last_seen)}s"),
]


def _parse_mac(mac: str) -> bytes:
    """Convert a display MAC ('AA:BB:CC:DD:EE:FF') to Sniffle's target byte order.

    Sniffle's `targ_mac`/`cmd_mac` take the 6 bytes least-significant-first — the reverse
    of the colon display form — matching the upstream `sniff_receiver.py`.
    """
    parts = mac.replace("-", ":").split(":")
    if len(parts) != 6:
        raise ValueError(f"MAC must be 6 colon-separated bytes, got {mac!r}")
    return bytes(int(h, 16) for h in reversed(parts))


def export_pcap(record: "ConnectionRecord", path: str | None = None) -> str:
    """Write a followed connection's buffered packets to a PCAP for Wireshark.

    The capture starts at the `CONNECT_IND` (which carries the access address, CRC init and
    channel map a dissector needs) and includes the followed data-channel packets, so an
    unencrypted link's ATT/GATT exchange dissects directly. DLT is BLUETOOTH_LE_LL_WITH_PHDR
    via `sniffle.pcap.PcapBleWriter`. Returns the written path.
    """
    os.makedirs("pcap", exist_ok=True)
    if path is None:
        path = f"pcap/sniffle_conn_{record.aa:08X}_{int(time.time())}.pcap"
    writer = PcapBleWriter(path)
    try:
        for pkt in record.packets:
            writer.write_packet_message(pkt)
    finally:
        writer.close()
    return path


# --- Scan engine ------------------------------------------------------------

class SniffleScanner:
    """Runs the Sonoff/Sniffle dongle in a thread — advertising scan or connection follow.

    A daemon thread blocks on `recv_and_decode()` and folds each decoded message into a
    record, exactly like `scan_live.LiveScanner`. The firmware keeps sniffing regardless of
    our UI state, so `pause()` simply stops the thread from *updating* records (it keeps
    draining the serial so no backlog builds) rather than halting the radio — there is no
    connect-on-the-same-device invariant here as there is for the nRF52. All Sniffle/pyserial
    logging is routed to a file so nothing corrupts the curses screen.

    Two modes, one class (the open/thread/pause/close/logging plumbing is identical):

    - **advertising** (``follow=False``): per-address `SniffleRecord`s in ``self.devices``.
    - **connection follow** (``follow=True``): `SnifferMode.CONN_FOLLOW`; `CONNECT_IND`s
      become `ConnectionRecord`s in ``self.connections`` keyed by access address, and the
      followed data-channel packets update their counters. Advertising records are still
      kept so a `CONNECT_IND` can borrow the peripheral's advertised name. A ``targ_mac``/
      ``targ_irk`` restricts following to one peer and enables 3-channel hopping (``hop3``).
    """

    def __init__(self, port: str | None = None, active: bool = False,
                 follow: bool = False, targ_mac: bytes | None = None,
                 targ_irk: bytes | None = None):
        self._port = port
        self._active = active
        self._follow = follow
        self._targ_mac = targ_mac
        self._targ_irk = targ_irk
        self.devices: dict[str, SniffleRecord] = {}
        self.connections: dict[int, ConnectionRecord] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._scanning = threading.Event()
        self._hw: SniffleHW | None = None
        self._thread: threading.Thread | None = None

    @property
    def following(self) -> bool:
        return self._follow

    def open(self) -> None:
        port = self._port or find_sonoff_port()
        # Dedicated file logger: SniffleHW's default TrivialLogger prints to stderr, which
        # would corrupt the curses table. Disable propagation so nothing reaches the root
        # handler either.
        logger = logging.getLogger("sniffle")
        logger.handlers = [logging.FileHandler("sniff.log", mode="w")]
        logger.setLevel(logging.WARNING)
        logger.propagate = False
        self._hw = SniffleHW(serport=port, logger=logger)
        self._setup()

    def _setup(self) -> None:
        """Push the current mode/target to the (already open) dongle.

        Split out from `open()` so `reconfigure()` can re-run it on the same open HW when
        the operator switches from advertising scan to following a selected device — the
        whole point of the scan→select→follow flow. Safe to call only when the receive
        thread is not running (it reads/writes the same serial port)."""
        assert self._hw is not None
        if self._follow:
            # Follow mode: hop across the 3 primary adv channels only when we have a target
            # to lock onto (hop3 requires a target). With no target we camp on one channel
            # and catch whatever CONNECT_IND lands there, attributing after the fact.
            hop3 = bool(self._targ_mac or self._targ_irk)
            self._hw.setup_sniffer(mode=SnifferMode.CONN_FOLLOW, chan=37,
                                   targ_mac=self._targ_mac, targ_irk=self._targ_irk,
                                   hop3=hop3, ext_adv=False,
                                   phy_preload=PhyMode.PHY_2M, validate_crc=True)
        else:
            mode = SnifferMode.ACTIVE_SCAN if self._active else SnifferMode.PASSIVE_SCAN
            self._hw.setup_sniffer(mode=mode, chan=37, hop3=False, ext_adv=False,
                                   phy_preload=PhyMode.PHY_2M, validate_crc=True)
        self._hw.mark_and_flush()  # drop stale buffered packets from before setup

    def reconfigure(self, *, follow: bool, targ_mac: bytes | None = None,
                    targ_irk: bytes | None = None) -> None:
        """Switch the open dongle between advertising scan and following one peer.

        This is the scan→select→follow bridge: the operator sees a device in the live
        advertising table and follows it by its *current* address (captured at selection
        time — crucial for RPA peers whose address rotates, so "see it, chase it"), with
        3-channel hopping to reliably catch its CONNECT_IND. The receive thread is stopped
        before reconfiguring the serial and restarted after; a fresh follow session starts
        with an empty connection map.
        """
        self._stop_recv_thread()
        self._follow = follow
        self._targ_mac = targ_mac
        self._targ_irk = targ_irk
        with self._lock:
            self.connections.clear()
        self._setup()
        self.start()

    def start(self) -> None:
        self._stop.clear()  # may have been set by a previous reconfigure()
        self._scanning.set()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _stop_recv_thread(self) -> None:
        """Stop and join the receive thread without closing the serial port.

        Used by `reconfigure()` to free the serial for a fresh `setup_sniffer`; `close()`
        does the same before resetting and closing the port. `cancel_recv` unblocks the
        thread's blocking read (SniffleHW clears its own `recv_cancelled` on the way out,
        so the port is immediately reusable)."""
        self._stop.set()
        if self._hw is not None:
            try:
                self._hw.cancel_recv()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def _run(self) -> None:
        assert self._hw is not None
        while not self._stop.is_set():
            try:
                msg = self._hw.recv_and_decode()
            except Exception:
                break  # serial closed / cancelled during teardown
            if msg is None:
                continue
            # Drain-without-update while paused: keep reading so the serial never backs
            # up, but leave the records (and the table) frozen.
            if not self._scanning.is_set():
                continue
            if self._follow:
                self._handle_follow(msg)
            elif isinstance(msg, (AdvaMessage, AdvDirectIndMessage)) and not msg.crc_err:
                self._update_adv(msg)

    def _update_adv(self, msg) -> None:
        """Fold one advertising/scan-response message into its per-address record."""
        key = str_mac(msg.AdvA)
        with self._lock:
            rec = self.devices.get(key)
            if rec is None:
                rec = SniffleRecord(address=key)
                self.devices[key] = rec
            rec.update(msg)

    def _handle_follow(self, msg) -> None:
        """Dispatch a decoded message in connection-follow mode.

        Advertising frames still feed `self.devices` (so a later CONNECT_IND can borrow the
        peripheral's advertised name); `CONNECT_IND`s create the connection record; the
        followed data-channel packets update its counters and encryption/termination state.
        """
        if isinstance(msg, (AdvaMessage, AdvDirectIndMessage)):
            if not msg.crc_err:
                self._update_adv(msg)
        elif isinstance(msg, ConnectIndMessage):
            if not msg.crc_err:
                self._on_connect(msg)
        elif isinstance(msg, DataMessage):
            self._on_data(msg)

    def _on_connect(self, msg: ConnectIndMessage) -> None:
        """Record a newly observed connection (CONNECT_IND) — the phone↔device link."""
        peripheral = str_mac(msg.AdvA)
        with self._lock:
            rec = self.connections.get(msg.aa_conn)
            if rec is None:
                # Count used data channels in the 37-bit channel map (5 bytes, LE).
                channels = bin(int.from_bytes(msg.ChM, "little")).count("1")
                name = ""
                adv = self.devices.get(peripheral)
                if adv is not None:
                    name = adv.name
                rec = ConnectionRecord(
                    aa=msg.aa_conn,
                    central=str_mac(msg.InitA),
                    central_type=_addr_type_short(msg.InitA, msg.TxAdd),
                    peripheral=peripheral,
                    peripheral_type=_addr_type_short(msg.AdvA, msg.RxAdd),
                    peripheral_name=name,
                    interval_ms=msg.Interval * 1.25,
                    latency=msg.Latency,
                    timeout_ms=msg.Timeout * 10,
                    hop=msg.Hop,
                    channels=channels,
                )
                rec.packets.append(msg)  # the CONNECT_IND: carries AA/CRCInit/ChM for dissection
                self.connections[msg.aa_conn] = rec
            rec.rssi = msg.rssi
            rec.last_seen = time.time()

    def _on_data(self, msg: DataMessage) -> None:
        """Fold a followed data-channel packet into its connection record."""
        with self._lock:
            rec = self.connections.get(msg.aa)
            if rec is None:
                return  # data for a connection whose CONNECT_IND we never saw
            rec.last_seen = time.time()
            if len(rec.packets) < _CONN_PCAP_CAP:
                rec.packets.append(msg)
            if msg.data_dir:  # 1 = peripheral -> central
                rec.pkt_peripheral += 1
            else:             # 0 = central -> peripheral
                rec.pkt_central += 1
            # Enriching the peripheral name late (it may have advertised between the
            # CONNECT_IND and now): keep the first non-empty we learn.
            if not rec.peripheral_name:
                adv = self.devices.get(rec.peripheral)
                if adv is not None and adv.name:
                    rec.peripheral_name = adv.name
            if isinstance(msg, LlControlMessage):
                if msg.opcode in (0x03, 0x05):  # LL_ENC_REQ / LL_START_ENC_REQ
                    rec.encrypted = True
                elif msg.opcode == 0x02:         # LL_TERMINATE_IND
                    rec.terminated = True

    def pause(self) -> None:
        self._scanning.clear()

    def resume(self) -> None:
        self._scanning.set()

    @property
    def is_scanning(self) -> bool:
        return self._scanning.is_set()

    def snapshot(self) -> list:
        """Records for the live table: connections in follow mode, else adv devices."""
        with self._lock:
            if self._follow:
                return list(self.connections.values())
            return list(self.devices.values())

    def close(self) -> None:
        self._scanning.clear()
        self._stop_recv_thread()
        if self._hw is not None:
            try:
                self._hw.cmd_reset()
            except Exception:
                pass
            try:
                self._hw.ser.close()
            except Exception:
                pass

    def save_csv(self) -> str:
        os.makedirs("csv", exist_ok=True)
        if self._follow:
            return self._save_conn_csv()
        path = f"csv/sniffle_{int(time.time())}.csv"
        headers = [h for h, _, _ in COLUMNS] + ["Services", "FirstSeen"]
        with self._lock, open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            # Discovery order (dict insertion order) to match the on-screen table.
            for d in self.devices.values():
                row = [func(d) for _, _, func in COLUMNS]
                row.append(";".join(sorted(d.services)))
                row.append(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d.first_seen)))
                writer.writerow(row)
        return path

    def _save_conn_csv(self) -> str:
        """Write the connection-follow table to csv/sniffle_conn_*.csv (discovery order)."""
        path = f"csv/sniffle_conn_{int(time.time())}.csv"
        headers = [h for h, _, _ in CONN_COLUMNS] + ["AccessAddr", "Latency",
                                                      "TimeoutMs", "Channels", "FirstSeen"]
        with self._lock, open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for c in self.connections.values():
                row = [func(c) for _, _, func in CONN_COLUMNS]
                row.append(f"0x{c.aa:08X}")
                row.append(str(c.latency))
                row.append(f"{c.timeout_ms:.0f}")
                row.append(str(c.channels))
                row.append(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(c.first_seen)))
                writer.writerow(row)
        return path


def run(port: str | None = None, active: bool = False, follow: bool = False,
        targ_mac: bytes | None = None, targ_irk: bytes | None = None):
    """Entry point: open the Sonoff/Sniffle dongle and start the live UI.

    In follow mode the table shows connection records (and CONN_COLUMNS); otherwise it
    shows advertising records. Returns the record the operator selected, or None.
    """
    print("Opening the Sniffle dongle...")
    scanner = SniffleScanner(port, active=active, follow=follow,
                             targ_mac=targ_mac, targ_irk=targ_irk)
    scanner.open()
    scanner.start()
    columns = CONN_COLUMNS if follow else COLUMNS
    title = "Sniffle — connection follow" if follow else "Sniffle"
    if follow:
        print("Following connections: start the sniffer, then trigger a reconnection "
              "(toggle BT / power-cycle the device) to capture its CONNECT_IND.")
    try:
        selected = livetable.run(scanner, columns, title=title)
    finally:
        scanner.close()
    if follow:
        print(f"Follow finished. {len(scanner.connections)} connections seen.")
    else:
        print(f"Scan finished. {len(scanner.devices)} devices seen.")
    return selected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default=None,
                        help="Sonoff serial port (default: autodetect the ITead by-id link)")
    parser.add_argument("--active", action="store_true",
                        help="Active scan: transmit SCAN_REQ to elicit names (default: passive)")
    parser.add_argument("--follow", action="store_true",
                        help="Connection-follow mode: capture CONNECT_IND + followed link")
    parser.add_argument("--target", default=None, metavar="MAC",
                        help="Follow only this peer MAC (enables 3-channel hopping)")
    parser.add_argument("--irk", default=None, metavar="HEX",
                        help="Follow only the peer with this IRK (hex; for RPA devices)")
    args = parser.parse_args()
    if (args.target or args.irk) and not args.follow:
        parser.error("--target/--irk require --follow")
    if args.target and args.irk:
        parser.error("specify at most one of --target / --irk")
    targ_mac = _parse_mac(args.target) if args.target else None
    targ_irk = bytes.fromhex(args.irk) if args.irk else None
    run(args.port, active=args.active, follow=args.follow,
        targ_mac=targ_mac, targ_irk=targ_irk)
