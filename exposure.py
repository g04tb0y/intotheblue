"""Passive exposure triage for a BLE device seen in the live scan.

Read-only classification of a device's attack surface using ONLY the advertising
data already collected by the scanner. It sends no packets — no connection, no
flood, no bond — and implements no attack. It exists to help a tester prioritise
which devices deserve a scoped, authorised active test with a dedicated tool.
"""
from __future__ import annotations

_FOOTER = ("Passive assessment from advertising only — no packets sent. "
           "Confirming exploitability requires a scoped, authorised active test.")

_ADDR_NOTES = {
    "pub":   ("public", "Fixed factory device address (OUI-based, like a MAC), sent in the "
                        "clear and never rotated — persistent identifier, trackable."),
    "rnd-s": ("random static", "Stable random address — persists until reboot, partially trackable."),
    "rnd-r": ("resolvable RPA", "Rotating private address — privacy mitigation active."),
    "rnd-n": ("non-resolvable", "Rotating non-resolvable address — privacy mitigation active."),
}


def assess(rec) -> list[tuple[str, str, str]]:
    """Return a list of (label, value, note) passive exposure signals."""
    rows: list[tuple[str, str, str]] = []

    # Fast Pair (FE2C) — target class for the Fast Pair attack family
    if not rec.fast_pair:
        rows.append(("Fast Pair (FE2C)", "not advertised",
                     "Out of the Fast Pair target class (CVE-2025-36911 and related)."))
    elif rec.fast_pair == "Yes":
        rows.append(("Fast Pair (FE2C)", "advertised (non-discoverable)",
                     "In the Fast Pair target class; not currently in pairing/discoverable mode."))
    else:
        rows.append(("Fast Pair (FE2C)", f"discoverable, model id {rec.fast_pair}",
                     "In the Fast Pair target class and in discoverable (pairing) mode."))

    # Address type — identity exposure / trackability
    value, note = _ADDR_NOTES.get(rec.addr_type, (rec.addr_type or "unknown", ""))
    rows.append(("Address type", value, note))

    # Connectable — prerequisite for any connection-based check
    rows.append(("Connectable", "yes" if rec.connectable else "no",
                 "Accepts connections." if rec.connectable
                 else "Broadcast-only in the observed window."))

    # Context (identification only)
    if rec.name:
        rows.append(("Name", rec.name, ""))
    if rec.manufacturer:
        rows.append(("Manufacturer", rec.manufacturer, ""))
    if rec.protocol:
        rows.append(("Protocol/type", rec.protocol, ""))
    if rec.services:
        rows.append(("Advertised services", ", ".join(sorted(rec.services)), ""))

    return rows


def _verdict(rec) -> str:
    in_class = bool(rec.fast_pair)
    if not in_class:
        return "Out of the Fast Pair attack class — low priority for this vuln family."
    parts = ["In Fast Pair class"]
    if rec.fast_pair != "Yes":
        parts.append("discoverable")
    if rec.connectable:
        parts.append("connectable")
    if rec.addr_type == "pub":
        parts.append("public identity address")
    priority = "high" if (rec.connectable and rec.fast_pair != "Yes") else "medium"
    return f"{' + '.join(parts)} → {priority}-priority candidate for authorised active testing."


def report(rec) -> str:
    """Human-readable passive exposure report for a scan record."""
    lines = [f"Fast Pair GATT exposure check — {rec.address}  {rec.name or '(no name)'}", ""]
    for label, value, note in assess(rec):
        lines.append(f"  {label:20} {value}")
        if note:
            lines.append(f"  {'':20} {note}")
    lines.append("")
    lines.append(f"  Verdict: {_verdict(rec)}")
    lines.append("")
    lines.append(f"  {_FOOTER}")
    return "\n".join(lines)
