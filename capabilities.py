"""BLE capability fingerprint — detect which known capabilities a device exposes.

Detection only: it reads the connected device's GATT database (services and
characteristics) and matches it against a registry of known capability signatures.
It never writes, triggers, or invokes anything. `analyze`/`report` are pure and
work on any objects exposing `.uuid` (services and characteristics) and the
characteristic `.writable` / `.writable_without_response` flags.

To add a capability, add one row to _CAPABILITIES.
"""
from __future__ import annotations

# (label, {service UUID signatures, normalised lowercase str}, note)
# 16-bit UUIDs are their 4-hex short form ("180a"); vendor UUIDs the full 128-bit str.
_CAPABILITIES: list[tuple[str, set[str], str]] = [
    ("Firmware update (DFU/OTA)", {
        "fe59",                                    # Nordic Secure DFU
        "00001530-1212-efde-1523-785feabcd123",    # Nordic Legacy DFU
        "fef5",                                    # Dialog / Renesas SUOTA
        "1d14d6ee-fd63-4fa1-bfa4-8f47b42119f0",    # Silicon Labs OTA
        "f000ffc0-0451-4000-b000-000000000000",    # TI OAD
    }, "Exposes an over-the-air firmware update surface."),
    ("HID over GATT", {"1812"},
     "Acts as a HID device (keyboard/mouse/…) — HID attack surface."),
    ("Nordic UART (serial passthrough)", {"6e400001-b5a3-f393-e0a9-e50e24dcca9e"},
     "Serial / command channel over BLE."),
    ("Object Transfer", {"1825"}, "File / object transfer service."),
    ("Device Information", {"180a"},
     "Firmware/hardware/software revision, PnP ID, model — precise fingerprinting."),
    ("Battery", {"180f"}, "Battery level service."),
    ("Bluetooth Mesh", {"1827", "1828"}, "Mesh provisioning / proxy."),
    ("IP Support (IPSP)", {"1820"}, "IPv6 over BLE."),
    ("LE Audio", {"184e", "1850", "184f", "1851", "1852", "1853", "1846"},
     "LE Audio stream/capabilities/broadcast."),
    ("Media / Telephony control", {"1848", "1849", "184b", "184c"},
     "Media Control / Telephone Bearer / call control."),
    ("Human Interface (scan params)", {"1813"}, "Scan Parameters — often paired with HID."),
]

# Characteristics that indicate a remotely triggerable DFU entry point
_DFU_CONTROL_CHARS = {
    "8ec90003-f315-4f60-9fb8-838830daea50",   # Nordic buttonless DFU (unbonded)
    "8ec90004-f315-4f60-9fb8-838830daea50",   # Nordic buttonless DFU (bonded)
    "00001531-1212-efde-1523-785feabcd123",   # Nordic legacy DFU control point
}


def _norm(uuid) -> str:
    return str(uuid).lower()


def analyze(services):
    """Return (detected, flags, n_services, n_chars) for a GATT service list."""
    svc_uuids: set[str] = set()
    chars = []
    for service in services:
        svc_uuids.add(_norm(service.uuid))
        for char in service.characteristics:
            chars.append(char)
    char_uuids = {_norm(c.uuid) for c in chars}

    detected = [(label, note) for label, sigs, note in _CAPABILITIES if svc_uuids & sigs]

    flags = []
    if _DFU_CONTROL_CHARS & char_uuids:
        flags.append("Buttonless/remote DFU control characteristic present.")
    writable = sum(1 for c in chars
                   if getattr(c, "writable", False) or getattr(c, "writable_without_response", False))
    if writable:
        flags.append(f"{writable} writable characteristic(s) exposed.")
    vendor = sum(1 for u in svc_uuids if len(u) > 6)  # 128-bit (dashed) vs 16-bit short form
    if vendor:
        flags.append(f"{vendor} vendor (128-bit) service(s).")

    return detected, flags, len(svc_uuids), len(chars)


def report(services) -> str:
    detected, flags, n_svc, n_char = analyze(services)
    lines = ["Capability fingerprint", "", f"  {n_svc} services, {n_char} characteristics", ""]
    if detected:
        lines.append("  Detected capabilities:")
        lines += [f"    + {label} — {note}" for label, note in detected]
    else:
        lines.append("  No known capability signatures matched.")
    if flags:
        lines.append("")
        lines.append("  Attack-surface notes:")
        lines += [f"    - {f}" for f in flags]
    lines.append("")
    lines.append("  Detection only — read from the GATT database, nothing invoked.")
    return "\n".join(lines)


# --- Classic (BR/EDR) SDP profiles ------------------------------------------
# (label, {16-bit SDP service class UUIDs}, note)
_CLASSIC_PROFILES: list[tuple[str, set[int], str]] = [
    ("Audio streaming (A2DP)", {0x110A, 0x110B, 0x110D}, "Advanced Audio Distribution (sink/source)."),
    ("Remote control (AVRCP)", {0x110E, 0x110F}, "A/V remote control."),
    ("Hands-free / Headset", {0x1108, 0x1112, 0x111E, 0x111F}, "Voice call audio (HFP/HSP)."),
    ("Serial Port (SPP)", {0x1101}, "RFCOMM serial channel — common command/debug interface."),
    ("Dial-up Networking (DUN)", {0x1103}, "Modem / dial-up networking."),
    ("File transfer (OBEX)", {0x1105, 0x1106}, "Object Push / File Transfer."),
    ("Phonebook (PBAP)", {0x112F, 0x1130}, "Phonebook access."),
    ("Messaging (MAP)", {0x1132, 0x1134}, "Message access."),
    ("HID", {0x1124}, "Human Interface Device — input attack surface."),
    ("Networking (PAN)", {0x1115, 0x1116, 0x1117}, "Personal Area Network / BNEP."),
    ("SIM Access (SAP)", {0x112D}, "SIM access."),
    ("Device ID (PnP)", {0x1200}, "Device identification record."),
]


def classic_report(uuid16s: set[int], vendor_count: int = 0) -> str:
    """Report Classic capabilities from a set of 16-bit SDP service-class UUIDs."""
    detected = [(label, note) for label, sigs, note in _CLASSIC_PROFILES if uuid16s & sigs]
    lines = ["Classic profile capabilities", "",
             f"  {len(uuid16s)} SDP service class(es)"
             + (f", {vendor_count} vendor/other UUID(s)" if vendor_count else ""), ""]
    if detected:
        lines.append("  Detected profiles:")
        lines += [f"    + {label} — {note}" for label, note in detected]
    elif uuid16s or vendor_count:
        lines.append("  SDP records present but no known profile signatures matched.")
    else:
        lines.append("  No SDP profiles cached — connect or run `sdptool browse <addr>` to populate.")
    lines.append("")
    lines.append("  Detection only — read from BlueZ SDP records, nothing invoked.")
    return "\n".join(lines)
