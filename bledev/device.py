"""Open the nRF52 Connectivity dongle with serial-port autodetection."""
from __future__ import annotations

import glob
import os
import time

from blatann import BleDevice
from pc_ble_driver_py.exceptions import NordicSemiException

_FALLBACK_PORT = "/dev/ttyACM0"


def find_dongle_port() -> str:
    """Locate the Nordic nRF52 Connectivity dongle's port.

    Looks first for a stable link under /dev/serial/by-id containing 'nRF52'
    (independent of USB enumeration order). Falls back to /dev/ttyACM0.
    """
    matches = glob.glob("/dev/serial/by-id/*nRF52*")
    if matches:
        # Resolve the symlink to the real /dev/ttyACMx
        return os.path.realpath(sorted(matches)[0])
    return _FALLBACK_PORT


def open_device(port: str | None = None, configure: bool = False, retries: int = 3) -> BleDevice:
    """Create and open a BleDevice on the given (or autodetected) port.

    With configure=True it calls BleDevice.configure() before open(): required
    for the peripheral role (sizes the GATT server, vendor UUIDs, etc.), which
    must be configured *before* opening.

    Opening the dongle can fail with NrfError.timeout if a previous session did
    not release the port cleanly: we retry a few times, recreating the BleDevice
    each time (needed because the driver is left in an inconsistent state after a
    failed open).
    """
    if port is None:
        port = find_dongle_port()
    last_error: Exception | None = None
    for attempt in range(retries):
        ble_device = BleDevice(port)
        try:
            if configure:
                ble_device.configure()
            ble_device.open()
            return ble_device
        except NordicSemiException as err:
            last_error = err
            try:
                ble_device.close()
            except Exception:
                pass
            if attempt < retries - 1:
                time.sleep(2)
    raise RuntimeError(f"Could not open the dongle on {port} after {retries} attempts") from last_error
