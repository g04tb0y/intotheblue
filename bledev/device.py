"""Apertura del dongle nRF52 Connectivity con autorilevamento della porta seriale."""
from __future__ import annotations

import glob
import os
import time

from blatann import BleDevice
from pc_ble_driver_py.exceptions import NordicSemiException

_FALLBACK_PORT = "/dev/ttyACM0"


def find_dongle_port() -> str:
    """Individua la porta del dongle Nordic nRF52 Connectivity.

    Cerca prima un link stabile in /dev/serial/by-id contenente 'nRF52'
    (indipendente dall'ordine di enumerazione USB). Se non trova nulla,
    ripiega su /dev/ttyACM0.
    """
    matches = glob.glob("/dev/serial/by-id/*nRF52*")
    if matches:
        # Risolve il symlink verso il vero /dev/ttyACMx
        return os.path.realpath(sorted(matches)[0])
    return _FALLBACK_PORT


def open_device(port: str | None = None, configure: bool = False, retries: int = 3) -> BleDevice:
    """Crea e apre un BleDevice sulla porta indicata (o autorilevata).

    Con configure=True chiama BleDevice.configure() prima di open(): necessario
    per il ruolo peripheral (dimensiona GATT server, UUID vendor, ecc.), che va
    configurato *prima* dell'apertura.

    L'apertura del dongle può fallire con NrfError.timeout se una sessione
    precedente non ha rilasciato la porta pulita: si ritenta qualche volta
    ricreando l'oggetto BleDevice (necessario perché il driver resta in stato
    inconsistente dopo un open fallito).
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
    raise RuntimeError(f"Impossibile aprire il dongle su {port} dopo {retries} tentativi") from last_error
