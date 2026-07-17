"""Live BLE scan: full-screen table that updates continuously.

The dongle scans continuously (restarting scan chunks); every advertising packet
updates a per-address record. The screen shows a table sorted by RSSI and, at the
bottom, a status bar with the commands:

    ^P  pause/resume scan      ^S  save to CSV      Q  quit

The column set lives in COLUMNS: adding a field = add an attribute to DeviceRecord
(with its update logic) and one entry to COLUMNS.
"""
from __future__ import annotations

import csv
import logging
import os
import threading
import time
from dataclasses import dataclass, field

from blatann.gap.advertise_data import AdvertisingPacketType

import livetable
from bledev import get_manufacturer, open_device

# Continuous scanning is achieved by restarting chunks of this duration
_SCAN_CHUNK_SECONDS = 5
_CONNECTABLE = {AdvertisingPacketType.connectable_undirected, AdvertisingPacketType.connectable_directed}

# Compact labels for the BLE address type
_ADDR_TYPE_SHORT = {
    "public": "pub",
    "static": "rnd-s",
    "res": "rnd-r",
    "nonres": "rnd-n",
    "anonymous": "anon",
}

# 16-bit "member service" UUIDs commonly seen in Service Data, mapped to the
# vendor/protocol they identify. Extend freely.
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

# Apple manufacturer-data message types (company id 0x004C, byte at index 2)
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


def _addr_type_short(report) -> str:
    return _ADDR_TYPE_SHORT.get(report.peer_address.get_addr_type_str(), "?")


def _decode_protocol(adv) -> str:
    """Human-readable protocol/product label from manufacturer or service data."""
    md = adv.manufacturer_data
    if md and len(md) >= 2:
        cid = int.from_bytes(md[:2], "little")
        if cid == 0x004C and len(md) >= 3:  # Apple
            return "Apple " + _APPLE_MSG_TYPES.get(md[2], f"0x{md[2]:02X}")
        if cid == 0x0006:  # Microsoft
            return "MS CDP/SwiftPair"
        if cid == 0x0075:  # Samsung
            return "Samsung"
    sd = adv.service_data
    if sd and len(sd) >= 2:
        uuid16 = int.from_bytes(sd[:2], "little")
        return _SERVICE_DATA_UUIDS.get(uuid16, f"svc 0x{uuid16:04X}")
    return ""


# --- Data model -------------------------------------------------------------

@dataclass
class DeviceRecord:
    address: str
    addr_type: str = "?"
    name: str = ""
    peer: object = None      # blatann BLEGapAddr, kept so we can reconnect precisely
    rssi: int = 0
    manufacturer: str = ""
    protocol: str = ""
    connectable: bool = False
    services: set = field(default_factory=set)
    flags: int | None = None
    count: int = 0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    def update(self, report) -> None:
        """Update the record with a new ScanReport for the same address."""
        self.last_seen = time.time()
        self.count += 1
        self.rssi = report.rssi
        self.addr_type = _addr_type_short(report)
        self.peer = report.peer_address

        adv = report.advertise_data
        # report.device_name falls back to the address when there is no real name:
        # keep only a genuine local_name so we don't duplicate the Address column
        name = adv.local_name
        if name and name != self.address:
            self.name = name

        if report.packet_type in _CONNECTABLE:
            self.connectable = True

        if adv.manufacturer_data:
            mnf_id = int.from_bytes(adv.manufacturer_data[:2], "little")
            self.manufacturer = get_manufacturer(mnf_id)

        label = _decode_protocol(adv)
        if label:
            self.protocol = label

        for uuid in adv.service_uuids:
            self.services.add(str(uuid))

        if adv.flags is not None:
            self.flags = adv.flags


# --- Columns (extensible registry) ------------------------------------------
# (header, width, function(record) -> str)
COLUMNS: list[livetable.Column] = [
    ("Address",       19, lambda d: d.address.split(",")[0]),
    ("Addr",           6, lambda d: d.addr_type),
    ("Name",          14, lambda d: d.name),
    ("RSSI",           5, lambda d: str(d.rssi)),
    ("Conn",           4, lambda d: "Yes" if d.connectable else "-"),
    ("Manufacturer",  18, lambda d: d.manufacturer),
    ("Type",          17, lambda d: d.protocol),
    ("Svc",            4, lambda d: str(len(d.services)) if d.services else "-"),
    ("Pkt",            4, lambda d: str(d.count)),
    ("Age",            4, lambda d: f"{int(time.time() - d.last_seen)}s"),
]


# --- Scan engine ------------------------------------------------------------

class LiveScanner:
    """Runs the dongle's continuous scan in a thread, updating the records."""

    def __init__(self, port: str | None):
        self.devices: dict[str, DeviceRecord] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._scanning = threading.Event()
        self._port = port
        self._ble_device = None
        self._thread: threading.Thread | None = None

    def open(self) -> None:
        self._ble_device = open_device(self._port)
        self._ble_device.scanner.on_scan_received.register(self._on_report)

    @property
    def ble_device(self):
        """The open BleDevice, so interactions can reuse it (no close/reopen)."""
        return self._ble_device

    def start(self) -> None:
        self._scanning.set()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _on_report(self, scanner, report) -> None:
        key = str(report.peer_address)
        with self._lock:
            record = self.devices.get(key)
            if record is None:
                record = DeviceRecord(address=key)
                self.devices[key] = record
            record.update(report)

    def _run(self) -> None:
        scanner = self._ble_device.scanner
        scanner.set_default_scan_params(timeout_seconds=_SCAN_CHUNK_SECONDS, active_scanning=True)
        while not self._stop.is_set():
            if not self._scanning.is_set():
                time.sleep(0.1)
                continue
            try:
                scanner.start_scan(clear_scan_reports=False).wait()
            except Exception:
                break

    def pause(self) -> None:
        self._scanning.clear()
        try:
            self._ble_device.scanner.stop()
        except Exception:
            pass

    def resume(self) -> None:
        self._scanning.set()

    @property
    def is_scanning(self) -> bool:
        return self._scanning.is_set()

    def snapshot(self) -> list[DeviceRecord]:
        with self._lock:
            return list(self.devices.values())

    def close(self) -> None:
        self._stop.set()
        self.pause()
        if self._thread:
            self._thread.join(timeout=3)
        if self._ble_device:
            try:
                self._ble_device.close()
            except Exception:
                pass

    def save_csv(self) -> str:
        os.makedirs("csv", exist_ok=True)
        path = f"csv/scan_{int(time.time())}.csv"
        headers = [h for h, _, _ in COLUMNS] + ["Services", "FirstSeen"]
        with self._lock, open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for d in sorted(self.devices.values(), key=lambda r: r.rssi, reverse=True):
                row = [func(d) for _, _, func in COLUMNS]
                row.append(";".join(sorted(d.services)))
                row.append(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d.first_seen)))
                writer.writerow(row)
        return path


def run(port: str | None = None) -> None:
    """Entry point: open the dongle and start the live UI."""
    # Log to a file: nothing must land on the curses screen
    logging.basicConfig(filename="scan_live.log", filemode="w", level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger().handlers = [logging.FileHandler("scan_live.log", mode="w")]

    print("Opening the dongle...")
    scanner = LiveScanner(port)
    scanner.open()
    scanner.start()
    try:
        selected = livetable.run(scanner, COLUMNS, title="BLE")
    finally:
        scanner.close()

    print(f"Scan finished. {len(scanner.devices)} devices seen.")
    return selected


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=None, help="Dongle serial port (default: autodetect)")
    args = parser.parse_args()
    run(args.port)
