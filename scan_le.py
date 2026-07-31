"""Live BLE (LE) scan through the *host* Bluetooth adapter, via BlueZ.

This is the LE twin of `scan_classic.py`, and — like it — does **not** use the
nRF52 dongle: it drives `bluetoothctl scan le` on `hci0` and parses the same
event stream, so it subclasses `ClassicScanner` and only swaps the transport,
the record type and the property decoding.

Why a second BLE scan surface at all: host-side actions such as the identity
address disclosure check (`identity.py`) go through BlueZ `connect`/`pair`, and
they only work on devices present in **hci0's own** device cache. A device seen
by the dongle is invisible to BlueZ, so the scan that feeds those actions has to
run on the same adapter that will perform them.

Trade-off vs the dongle scan: BlueZ gives fewer advertising details (no raw PDU
type, so connectability is unknown; no service-data payload, so Fast Pair is
detected but its model id is not). Use the dongle scan for identification depth,
this one for host-side interaction.

Usage:
    python scan_le.py
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import exposure
import livetable
import scan_classic
# Direct module import (not the `bledev` package): this path must stay independent
# of blatann / the dongle driver, which `bledev/__init__` pulls in.
from bledev.manufacturers import get_manufacturer

# 16-bit UUID inside an `info` UUID line: 0000XXXX-0000-1000-8000-00805f9b34fb
_UUID16 = re.compile(r"\(0000([0-9a-fA-F]{4})-0000-1000-8000-00805f9b34fb\)")
# Any UUID inside an `info` UUID line
_UUID_ANY = re.compile(r"\(([0-9a-fA-F-]{36})\)")

_FAST_PAIR_UUID = 0xFE2C

# Service UUIDs that identify a product/protocol family. BlueZ does not expose the
# manufacturer payload the dongle scan decodes, so the advertised service UUID is the
# main identification handle here — it is what tells a phone apart from a sensor.
_SERVICE_LABELS = {
    0xFE2C: "Google Fast Pair",
    0xFEF3: "Google Nearby",
    0xFE9F: "Google",
    0xFD6F: "Exposure Notif.",
    0xFEAA: "Eddystone",
    0xFE95: "Xiaomi",
    0xFE07: "MS SwiftPair",
    0xFE08: "Microsoft",
    0xFEB9: "LG Electronics",
    0xFD5A: "Samsung",
    0xFE03: "Amazon",
    0xFDCD: "Tile/Life360",
}


# --- Data model -------------------------------------------------------------

@dataclass
class LeRecord:
    """A BLE device seen through BlueZ.

    Field names deliberately mirror `scan_live.DeviceRecord` so the passive
    `exposure` report works on both backends unchanged. `connectable` stays None
    here: BlueZ does not surface the advertising PDU type, and guessing it would
    put an unverified claim in a security report.
    """
    address: str
    addr_type: str = ""          # exposure vocabulary: pub / rnd-s / rnd-r / rnd-n
    bluez_type: str = ""         # what BlueZ reported: public / random
    name: str = ""
    rssi: int = 0
    manufacturer: str = ""
    protocol: str = ""
    fast_pair: str = ""
    connectable: bool | None = None
    connected: bool = False
    paired: bool = False
    services: set = field(default_factory=set)
    count: int = 0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


# --- Columns ----------------------------------------------------------------
COLUMNS: list[livetable.Column] = [
    ("Address",       19, lambda d: d.address),
    ("Addr",           6, lambda d: d.addr_type or "?"),
    ("Name",          18, lambda d: d.name),
    ("RSSI",           5, lambda d: str(d.rssi) if d.rssi else "-"),
    ("Type",          16, lambda d: d.protocol),
    ("FastPair",       8, lambda d: d.fast_pair or "-"),
    ("Manufacturer",  16, lambda d: d.manufacturer),
    ("Bond",           5, lambda d: "Yes" if d.paired else "-"),
    ("Svc",            4, lambda d: str(len(d.services)) if d.services else "-"),
    ("Seen",           5, lambda d: str(d.count)),
    ("Age",            4, lambda d: f"{int(time.time() - d.last_seen)}s"),
]


# --- Scan engine ------------------------------------------------------------

class LeScanner(scan_classic.ClassicScanner):
    """`bluetoothctl scan le` on the host adapter, rendered by the shared table."""

    record_cls = LeRecord
    csv_prefix = "le"

    def __init__(self):
        super().__init__(transport="le")

    @property
    def columns(self) -> list[livetable.Column]:
        return COLUMNS

    def _needs_info(self, rec) -> bool:
        # Address type, UUIDs and manufacturer data only come from `info`.
        return not rec.addr_type

    def _set_prop(self, rec, key: str, value: str) -> None:
        if key == "AddressType":
            rec.bluez_type = value
            rec.addr_type = exposure.addr_kind(rec.address, value)
        elif key == "UUID":
            self._add_uuid(rec, value)
        # BlueZ 5.85 emits "ManufacturerData.Key"; older/other paths use a space.
        elif key in ("ServiceData.Key", "ServiceData Key"):
            if _to_int(value) == _FAST_PAIR_UUID:
                rec.fast_pair = rec.fast_pair or "Yes"
        elif key in ("ManufacturerData.Key", "ManufacturerData Key"):
            mnf = _to_int(value)
            if mnf is not None:
                rec.manufacturer = get_manufacturer(mnf)
        elif key == "Connected":
            rec.connected = value.strip().lower() == "yes"
        elif key in ("Paired", "Bonded"):
            rec.paired = value.strip().lower() == "yes"   # latest state wins
        else:
            super()._set_prop(rec, key, value)

    @staticmethod
    def _add_uuid(rec, value: str) -> None:
        m16 = _UUID16.search(value)
        if m16:
            uuid16 = int(m16.group(1), 16)
            rec.services.add(f"0x{uuid16:04X}")
            if uuid16 == _FAST_PAIR_UUID:
                rec.fast_pair = rec.fast_pair or "Yes"
            rec.protocol = _SERVICE_LABELS.get(uuid16, rec.protocol)
            return
        m = _UUID_ANY.search(value)
        if m:
            rec.services.add(m.group(1).lower())

    def _extra_csv_headers(self) -> list[str]:
        return ["BlueZType", "Services", "FirstSeen"]

    def _extra_csv_row(self, d) -> list[str]:
        return [d.bluez_type,
                " ".join(sorted(d.services)),
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d.first_seen))]


def _to_int(value: str) -> int | None:
    """Parse a bluetoothctl key like '0x004c' / '0xfe2c'."""
    try:
        return int(value.strip().split()[0], 16)
    except (ValueError, IndexError):
        return None


def run():
    """Entry point: start LE discovery on the host adapter and show the live table."""
    scanner = LeScanner()
    scanner.start()
    try:
        selected = livetable.run(scanner, COLUMNS, title="BLE (BlueZ)")
    finally:
        scanner.close()

    print(f"Scan finished. {len(scanner.devices)} LE devices seen.")
    return selected


if __name__ == "__main__":
    run()
