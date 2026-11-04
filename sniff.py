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
devices). Connection following is Phase 2; this module is **Phase 1: the advertising
scan**, which proves the backend and slots it into the existing scan→select→act UI.

Boundary: the default mode is `PASSIVE_SCAN` — the radio only *listens*, it transmits
nothing. `ACTIVE_SCAN` (opt-in via ``active=True``) additionally sends `SCAN_REQ` to
elicit scan responses (names), so it is a transmitting mode and is labelled as such.

Sniffle's packet decoder output format is firmware-version-sensitive; re-validate against
hardware after any re-vendor of `sniffle/` (see `sniffle/VENDORED.md`).

Usage:
    python sniff.py [--port /dev/ttyUSB0] [--active]
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
    ScanRspMessage,
    str_mac,
)
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


# --- Scan engine ------------------------------------------------------------

class SniffleScanner:
    """Runs the Sonoff/Sniffle dongle's advertising sniff in a thread.

    A daemon thread blocks on `recv_and_decode()` and folds each advertising/scan-response
    message into a per-address record, exactly like `scan_live.LiveScanner`. The firmware
    keeps sniffing regardless of our UI state, so `pause()` simply stops the thread from
    *updating* records (it keeps draining the serial so no backlog builds) rather than
    halting the radio — there is no connect-on-the-same-device invariant here as there is
    for the nRF52. All Sniffle/pyserial logging is routed to a file so nothing corrupts
    the curses screen.
    """

    def __init__(self, port: str | None = None, active: bool = False):
        self._port = port
        self._active = active
        self.devices: dict[str, SniffleRecord] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._scanning = threading.Event()
        self._hw: SniffleHW | None = None
        self._thread: threading.Thread | None = None

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
        mode = SnifferMode.ACTIVE_SCAN if self._active else SnifferMode.PASSIVE_SCAN
        self._hw.setup_sniffer(mode=mode, chan=37, hop3=False, ext_adv=False,
                               phy_preload=PhyMode.PHY_2M, validate_crc=True)
        self._hw.mark_and_flush()  # drop stale buffered packets from before setup

    def start(self) -> None:
        self._scanning.set()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self._hw is not None
        while not self._stop.is_set():
            try:
                msg = self._hw.recv_and_decode()
            except Exception:
                break  # serial closed / cancelled during teardown
            if msg is None or not isinstance(msg, (AdvaMessage, AdvDirectIndMessage)):
                continue
            # Drain-without-update while paused: keep reading so the serial never backs
            # up, but leave the records (and the table) frozen.
            if not self._scanning.is_set():
                continue
            if msg.crc_err:
                continue
            key = str_mac(msg.AdvA)
            with self._lock:
                rec = self.devices.get(key)
                if rec is None:
                    rec = SniffleRecord(address=key)
                    self.devices[key] = rec
                rec.update(msg)

    def pause(self) -> None:
        self._scanning.clear()

    def resume(self) -> None:
        self._scanning.set()

    @property
    def is_scanning(self) -> bool:
        return self._scanning.is_set()

    def snapshot(self) -> list[SniffleRecord]:
        with self._lock:
            return list(self.devices.values())

    def close(self) -> None:
        self._stop.set()
        self._scanning.clear()
        if self._hw is not None:
            try:
                self._hw.cancel_recv()  # unblock the recv loop's blocking read
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)
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


def run(port: str | None = None, active: bool = False) -> SniffleRecord | None:
    """Entry point: open the Sonoff/Sniffle dongle and start the live UI."""
    print("Opening the Sniffle dongle...")
    scanner = SniffleScanner(port, active=active)
    scanner.open()
    scanner.start()
    try:
        selected = livetable.run(scanner, COLUMNS, title="Sniffle")
    finally:
        scanner.close()
    print(f"Scan finished. {len(scanner.devices)} devices seen.")
    return selected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default=None,
                        help="Sonoff serial port (default: autodetect the ITead by-id link)")
    parser.add_argument("--active", action="store_true",
                        help="Active scan: transmit SCAN_REQ to elicit names (default: passive)")
    args = parser.parse_args()
    run(args.port, active=args.active)
