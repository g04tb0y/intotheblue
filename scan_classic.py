"""Live Bluetooth Classic (BR/EDR) scan — for non-BLE devices.

Unlike the BLE tools, this does NOT use the nRF52 dongle: it drives the host's
Bluetooth adapter through BlueZ (`bluetoothctl scan bredr`) and parses the
discovery events. Reuses the shared live-table UI (livetable.py).

Classic devices only answer an inquiry while they are *discoverable* (pairing
mode); a device already connected/idle will not show up. The main identifier for
a Classic device is its Class of Device (CoD), decoded here into a readable label.

Usage:
    python scan_classic.py
"""
from __future__ import annotations

import csv
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field

import livetable

# Strips any ANSI/CSI escape sequence (colors, cursor moves, erase-line, ...)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# Leading interactive prompt, e.g. "[bluetoothctl]> " or "[JBL Go 3]# "
_PROMPT = re.compile(r"^\[[^\]]*\][#>] ")
# Matches a discovery event line after ANSI stripping (prompt prefix tolerated)
_EVENT = re.compile(r"\[(NEW|CHG|DEL)\] Device ([0-9A-F:]{17})(?: (.*?))?\s*$")
# Header of an `info <mac>` response block: "Device AA:BB:.. (public)"
_INFO_HEADER = re.compile(r"Device ([0-9A-F:]{17}) \((?:public|random)\)")


def _parse_rssi(value: str) -> int | None:
    """Parse RSSI from '-40' or '0xffffffd8 (-40)' forms."""
    m = re.search(r"\((-?\d+)\)", value)
    if m:
        return int(m.group(1))
    try:
        return int(value.split()[0])
    except (ValueError, IndexError):
        return None

# --- Class of Device decoding ----------------------------------------------
_MAJOR = {
    0x00: "Misc", 0x01: "Computer", 0x02: "Phone", 0x03: "Network",
    0x04: "Audio/Video", 0x05: "Peripheral", 0x06: "Imaging",
    0x07: "Wearable", 0x08: "Toy", 0x09: "Health", 0x1F: "Uncategorized",
}
_MINOR = {
    0x01: {0x01: "Desktop", 0x02: "Server", 0x03: "Laptop", 0x04: "PDA",
           0x05: "Palm", 0x06: "Wearable"},
    0x02: {0x01: "Cellular", 0x02: "Cordless", 0x03: "Smartphone",
           0x04: "Modem", 0x05: "ISDN"},
    0x04: {0x01: "Headset", 0x02: "Hands-free", 0x04: "Microphone",
           0x05: "Loudspeaker", 0x06: "Headphones", 0x07: "Portable Audio",
           0x08: "Car Audio", 0x09: "Set-top box", 0x0A: "HiFi Audio",
           0x0B: "VCR", 0x0C: "Video Camera", 0x0D: "Camcorder",
           0x0E: "Video Monitor", 0x10: "Video Conferencing"},
    0x07: {0x01: "Wristwatch", 0x02: "Pager", 0x03: "Jacket",
           0x04: "Helmet", 0x05: "Glasses"},
}


def decode_cod(cod: int) -> str:
    major = (cod >> 8) & 0x1F
    minor = (cod >> 2) & 0x3F
    major_name = _MAJOR.get(major, f"0x{major:02X}")
    minor_name = _MINOR.get(major, {}).get(minor)
    return f"{major_name} · {minor_name}" if minor_name else major_name


# --- Data model -------------------------------------------------------------

@dataclass
class ClassicRecord:
    address: str
    name: str = ""
    rssi: int = 0
    cod: int | None = None
    cod_label: str = ""
    icon: str = ""
    count: int = 0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


# --- Columns ----------------------------------------------------------------
COLUMNS: list[livetable.Column] = [
    ("Address",   19, lambda d: d.address),
    ("Name",      22, lambda d: d.name),
    ("Class",     26, lambda d: d.cod_label),
    ("Icon",      14, lambda d: d.icon),
    ("RSSI",       5, lambda d: str(d.rssi) if d.rssi else "-"),
    ("Seen",       5, lambda d: str(d.count)),
    ("Age",        4, lambda d: f"{int(time.time() - d.last_seen)}s"),
]


# --- Scan engine ------------------------------------------------------------

class ClassicScanner:
    """Drives `bluetoothctl` (BR/EDR discovery) and parses its event stream."""

    def __init__(self):
        self.devices: dict[str, ClassicRecord] = {}
        self._lock = threading.Lock()
        self._scanning = False
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._info_target: str | None = None   # device the current info block refers to
        self._info_requested: set[str] = set()  # devices we've already queried for details

    def start(self) -> None:
        self._proc = subprocess.Popen(
            ["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.resume()

    def _send(self, cmd: str) -> None:
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.write(cmd + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                pass

    def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        for raw in self._proc.stdout:
            line = _ANSI.sub("", raw).replace("\r", "")

            # Indented, prompt-less lines belong to the current `info` block
            if line[:1] in (" ", "\t"):
                if self._info_target and ": " in line:
                    key, _, value = line.strip().partition(": ")
                    self._apply_prop(self._info_target, key, value)
                continue

            line = _PROMPT.sub("", line).strip()

            m = _EVENT.search(line)
            if m:
                self._info_target = None
                self._handle_event(m.group(1), m.group(2), m.group(3))
                continue

            hm = _INFO_HEADER.search(line)
            if hm:
                self._info_target = hm.group(1)
            elif not line:
                self._info_target = None

    def _handle_event(self, event: str, address: str, rest: str | None) -> None:
        with self._lock:
            rec = self.devices.get(address)
            if rec is None:
                rec = ClassicRecord(address=address)
                self.devices[address] = rec
            rec.last_seen = time.time()
            rec.count += 1

            if event == "NEW" and rest and rest != address.replace(":", "-"):
                rec.name = rest  # rest is the alias
            elif event == "CHG" and rest and ": " in rest:
                key, _, value = rest.partition(": ")
                self._apply_prop(address, key, value, locked=True)

            need_details = rec.cod is None and address not in self._info_requested

        # The Class of Device only comes via `info` (not the CHG stream), so pull
        # the device details once. Done outside the lock to avoid holding it on I/O.
        if need_details:
            self._info_requested.add(address)
            self._send(f"info {address}")

    def _apply_prop(self, address: str, key: str, value: str, locked: bool = False) -> None:
        def _apply():
            rec = self.devices.get(address)
            if rec is None:
                return
            if key in ("Name", "Alias") and value and value != address.replace(":", "-"):
                if key == "Name" or not rec.name:
                    rec.name = value
            elif key == "RSSI":
                r = _parse_rssi(value)
                if r is not None:
                    rec.rssi = r
            elif key == "Class":
                try:
                    rec.cod = int(value.split()[0], 16)
                    rec.cod_label = decode_cod(rec.cod)
                except ValueError:
                    pass
            elif key == "Icon":
                rec.icon = value
            rec.last_seen = time.time()

        if locked:
            _apply()
        else:
            with self._lock:
                _apply()

    def pause(self) -> None:
        self._scanning = False
        self._send("scan off")

    def resume(self) -> None:
        self._scanning = True
        self._send("scan bredr")

    @property
    def is_scanning(self) -> bool:
        return self._scanning

    def snapshot(self) -> list[ClassicRecord]:
        with self._lock:
            return list(self.devices.values())

    def close(self) -> None:
        self._send("scan off")
        self._send("exit")
        if self._proc:
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.terminate()

    def save_csv(self) -> str:
        os.makedirs("csv", exist_ok=True)
        path = f"csv/classic_{int(time.time())}.csv"
        headers = [h for h, _, _ in COLUMNS] + ["CoD", "FirstSeen"]
        with self._lock, open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for d in sorted(self.devices.values(), key=lambda r: r.rssi, reverse=True):
                row = [func(d) for _, _, func in COLUMNS]
                row.append(f"0x{d.cod:06X}" if d.cod is not None else "")
                row.append(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d.first_seen)))
                writer.writerow(row)
        return path


def run() -> None:
    """Entry point: start BR/EDR discovery and show the live table."""
    scanner = ClassicScanner()
    scanner.start()
    try:
        livetable.run(scanner, COLUMNS, title="Classic")
    finally:
        scanner.close()

    print(f"Scan finished. {len(scanner.devices)} classic devices seen.")


if __name__ == "__main__":
    run()
