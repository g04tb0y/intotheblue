"""Emula un device BLE: il dongle fa da peripheral (advertising + GATT server).

Espone un servizio custom con tre caratteristiche, utile per collaudare app o
altri central (es. app nRF Connect, oppure client.py su un secondo dongle):

  - RX  (write)          il central scrive dei byte
  - TX  (notify)         il peripheral rimanda in notifica ciò che ha ricevuto (echo)
  - counter (read/notify) contatore che si incrementa ogni secondo

Uso:
    python emulate.py --name "TestDevice"
    python emulate.py --name "TestDevice" --duration 600
"""
from __future__ import annotations

import argparse
import struct
import threading
import time

from blatann.bt_sig.assigned_numbers import Appearance
from blatann.examples import example_utils
from blatann.gap import advertising
from blatann.gatt.gatts import GattsCharacteristicProperties
from blatann.uuid import Uuid128
from blatann.waitables import GenericWaitable

from bledev import open_device

logger = example_utils.setup_logger(level="INFO")

# UUID base custom (Nordic-like). Slot 2-3 identificano servizio/caratteristica.
SERVICE_UUID = Uuid128("bede0001-8dca-4c1e-9f2a-001122334455")
RX_CHAR_UUID = Uuid128("bede0002-8dca-4c1e-9f2a-001122334455")
TX_CHAR_UUID = Uuid128("bede0003-8dca-4c1e-9f2a-001122334455")
COUNTER_CHAR_UUID = Uuid128("bede0004-8dca-4c1e-9f2a-001122334455")


class CounterThread:
    """Aggiorna la caratteristica contatore una volta al secondo finché vive."""

    def __init__(self, characteristic):
        self.characteristic = characteristic
        self.count = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.wait(1.0):
            self.count = (self.count + 1) & 0xFFFFFFFF
            self.characteristic.set_value(struct.pack("<I", self.count), notify_client=True)

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)


def _on_rx_write(characteristic, event_args):
    data = event_args.value
    logger.info("RX <- %s ('%s')", data.hex(" "), data.decode("ascii", "replace"))
    # Echo verso la caratteristica TX (notifica al central se iscritto)
    _on_rx_write.tx_char.set_value(data, notify_client=True)


def _on_connect(peer, event_args):
    logger.info("Central connesso" if peer else "Connessione in timeout")


def _on_disconnect(peer, event_args):
    logger.info("Central disconnesso: %s", event_args.reason)


def main(port, name, duration):
    ble_device = open_device(port, configure=True)

    ble_device.generic_access_service.device_name = name
    ble_device.generic_access_service.appearance = Appearance.computer

    # Sicurezza aperta (nessun pairing) per massima interoperabilità in test
    from blatann.gap import IoCapabilities
    ble_device.client.security.set_security_params(
        passcode_pairing=False, bond=False, lesc_pairing=False,
        io_capabilities=IoCapabilities.DISPLAY_ONLY, out_of_band=False)

    service = ble_device.database.add_service(SERVICE_UUID)

    rx_char = service.add_characteristic(
        RX_CHAR_UUID,
        GattsCharacteristicProperties(read=False, write=True, write_no_response=True, max_length=64, variable_length=True),
        b"")
    tx_char = service.add_characteristic(
        TX_CHAR_UUID,
        GattsCharacteristicProperties(read=True, notify=True, max_length=64, variable_length=True),
        b"")
    counter_char = service.add_characteristic(
        COUNTER_CHAR_UUID,
        GattsCharacteristicProperties(read=True, notify=True, max_length=4, variable_length=False),
        [0, 0, 0, 0])

    _on_rx_write.tx_char = tx_char
    rx_char.on_write.register(_on_rx_write)

    counter_thread = CounterThread(counter_char)

    adv_data = advertising.AdvertisingData(local_name=name, flags=0x06)
    scan_data = advertising.AdvertisingData(
        service_uuid128s=SERVICE_UUID, has_more_uuid128_services=False,
        appearance=ble_device.generic_access_service.appearance)
    ble_device.advertiser.set_advertise_data(adv_data, scan_data)

    ble_device.client.on_connect.register(_on_connect)
    ble_device.client.on_disconnect.register(_on_disconnect)

    ble_device.advertiser.start(timeout_sec=0, auto_restart=True)
    logger.info("Advertising come '%s' (servizio %s) per %ds...", name, SERVICE_UUID, duration)

    try:
        GenericWaitable().wait(duration, exception_on_timeout=False)
    except KeyboardInterrupt:
        logger.info("Interrotto dall'utente")

    counter_thread.stop()
    logger.info("Stop advertising")
    ble_device.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default=None, help="Porta seriale del dongle (default: autorilevata)")
    parser.add_argument("--name", default="TestDevice", help="Nome advertising (default: TestDevice)")
    parser.add_argument("--duration", type=int, default=600, help="Durata in secondi (default: 600)")
    args = parser.parse_args()
    main(args.port, args.name, args.duration)
