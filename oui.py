"""Vendor lookup from a device's MAC OUI, using the local IEEE registry.

Public Bluetooth addresses -- all BR/EDR MACs, and BLE `public` addresses -- begin
with a 24-bit IEEE OUI that names the manufacturer even when no manufacturer data
is advertised. Random BLE addresses (static / resolvable / non-resolvable) carry
**no** OUI: their high bytes are random, so looking one up would invent a vendor.
Callers must gate on address kind; `lookup(..., random=True)` returns None as a guard.

Source, longest IEEE prefix first (as the registry intends): MA-S (36-bit) and
MA-M (28-bit) blocks before the 24-bit MA-L block, from the system `ieee-data`
package (/var/lib/ieee-data/*.csv). Falls back to nmap's OUI list, then to None.
Read-only; sends nothing. No blatann import, so every scan backend can use it.
"""
from __future__ import annotations

import csv
import os

# IEEE assignment CSVs, longest prefix first. Assignment is the hex prefix
# (9 nibbles = MA-S, 7 = MA-M, 6 = MA-L); Organization Name is the vendor.
_IEEE_CSVS = [
    "/var/lib/ieee-data/oui36.csv",   # MA-S, 36-bit
    "/var/lib/ieee-data/mam.csv",     # MA-M, 28-bit
    "/var/lib/ieee-data/oui.csv",     # MA-L, 24-bit
]
_NMAP_PREFIXES = "/usr/share/nmap/nmap-mac-prefixes"

# Cache: assignment-hex (upper) -> vendor name
_lookup: dict[str, str] | None = None
# Prefix lengths present in the loaded table, longest first
_prefix_lens: tuple[int, ...] = (9, 7, 6)


def _load_ieee() -> dict[str, str]:
    table: dict[str, str] = {}
    for path in _IEEE_CSVS:
        if not os.path.isfile(path):
            continue
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            next(reader, None)  # header: Registry,Assignment,Organization Name,...
            for row in reader:
                if len(row) >= 3 and row[1] and row[2]:
                    table.setdefault(row[1].strip().upper(), row[2].strip())
    return table


def _load_nmap() -> dict[str, str]:
    table: dict[str, str] = {}
    with open(_NMAP_PREFIXES, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("#") or " " not in line:
                continue
            prefix, _, vendor = line.partition(" ")
            prefix, vendor = prefix.strip().upper(), vendor.strip()
            if len(prefix) == 6 and vendor:
                table[prefix] = vendor
    return table


def _table() -> dict[str, str]:
    global _lookup
    if _lookup is None:
        table = _load_ieee()
        if not table and os.path.isfile(_NMAP_PREFIXES):
            table = _load_nmap()
        _lookup = table
    return _lookup


def _hex(address: str) -> str | None:
    """The MAC's hex digits (uppercase), or None if it isn't a MAC-like string."""
    mac = address.split(",")[0]                       # drop any blatann ',r' suffix
    digits = "".join(c for c in mac if c in "0123456789abcdefABCDEF").upper()
    return digits[:12] if len(digits) >= 6 else None


def lookup(address: str, random: bool = False) -> str | None:
    """Vendor for a public MAC, or None (unknown, random, or malformed).

    `random=True` short-circuits to None so a random BLE address is never
    mis-attributed to whatever vendor its bytes happen to collide with.
    """
    if random:
        return None
    digits = _hex(address)
    if digits is None:
        return None
    table = _table()
    for n in _prefix_lens:
        vendor = table.get(digits[:n])
        if vendor:
            return vendor
    return None
