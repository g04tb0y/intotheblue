"""Classic SPP (Serial Port Profile) serial console.

Finds the device's RFCOMM channel for the Serial Port service via SDP, then opens
an interactive console over it. The RFCOMM socket work runs in the system python3
(spp_console.py) because the project venv lacks AF_BLUETOOTH. The device must be
already PAIRED. Active interaction — the serial/command channel is the point of SPP.
"""
from __future__ import annotations

import os
import re
import subprocess

_CHANNEL = re.compile(r"Channel:\s*(\d+)")


def find_spp_channel(address: str) -> int | None:
    """Return the RFCOMM channel of the Serial Port (0x1101) service, or None."""
    try:
        out = subprocess.run(["sdptool", "browse", "--uuid", "0x1101", address],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = _CHANNEL.search(out)
    return int(m.group(1)) if m else None


def console(address: str) -> None:
    """Open an interactive SPP serial console to the (paired) device."""
    channel = find_spp_channel(address)
    if channel is None:
        print("  SPP RFCOMM channel not found via SDP (is the device paired and offering SPP?).")
        return
    print(f"  SPP found on RFCOMM channel {channel}.")
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spp_console.py")
    try:
        # Inherit the terminal so the console is interactive.
        subprocess.run(["python3", helper, address, str(channel)])
    except (OSError, subprocess.SubprocessError) as err:
        print(f"  Serial console failed: {err}")
