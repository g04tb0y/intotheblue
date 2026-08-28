"""Emulate a BLE device: the dongle acts as a peripheral (advertising + GATT server).

Exposes a custom service with three characteristics, useful to test apps or other
centrals (e.g. the nRF Connect app, or client.py on a second dongle):

  - RX  (write)          the central writes some bytes
  - TX  (notify)         the peripheral notifies back what it received (echo)
  - counter (read/notify) counter that increments every second

Usage:
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

# Custom base UUID (Nordic-like). Slots 2-3 identify service/characteristic.
SERVICE_UUID = Uuid128("bede0001-8dca-4c1e-9f2a-001122334455")
RX_CHAR_UUID = Uuid128("bede0002-8dca-4c1e-9f2a-001122334455")
TX_CHAR_UUID = Uuid128("bede0003-8dca-4c1e-9f2a-001122334455")
COUNTER_CHAR_UUID = Uuid128("bede0004-8dca-4c1e-9f2a-001122334455")


class CounterThread:
    """Updates the counter characteristic once per second while alive."""

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
    # Echo to the TX characteristic (notifies the central if subscribed)
    _on_rx_write.tx_char.set_value(data, notify_client=True)


def _on_connect(peer, event_args):
    logger.info("Central connected" if peer else "Connection timed out")


def _on_disconnect(peer, event_args):
    logger.info("Central disconnected: %s", event_args.reason)


def _on_pairing_complete(peer, event_args):
    # In positive-control mode this is the moment the identity is distributed: on a
    # successful Just Works bond the peripheral hands the central its IRK + identity
    # address, which is exactly what lets the central resolve the advertised RPA.
    logger.info("Pairing complete: %s", event_args)


def main(port, name, duration, positive_control=False):
    ble_device = open_device(port, configure=True)

    ble_device.generic_access_service.device_name = name
    ble_device.generic_access_service.appearance = Appearance.computer

    from blatann.gap import IoCapabilities
    if positive_control:
        # Deliberately weak peripheral: a *positive control* for the identity-address
        # disclosure check. It advertises a Resolvable Private Address and accepts an
        # unauthenticated Just Works bond, distributing its IRK + identity address —
        # the very behaviour a hardened Fast Pair accessory (e.g. the Redmi Buds 6
        # Play) refuses. Run identity.extract against this device's RPA and the host
        # should bond it and reveal the identity below.
        identity = ble_device.address
        logger.info("POSITIVE CONTROL — identity (expected disclosed) address: %s", identity)
        # RPA advertising. 900s update rate keeps the address stable across a test run.
        ble_device.set_privacy_settings(enabled=True, resolvable_address=True,
                                        update_rate_seconds=900)
        ble_device.client.security.set_security_params(
            passcode_pairing=False, bond=True, lesc_pairing=False,
            io_capabilities=IoCapabilities.NONE, out_of_band=False,
            reject_pairing_requests=False)
        ble_device.client.security.on_pairing_complete.register(_on_pairing_complete)
    else:
        # Open security (no pairing) for maximum interoperability in tests
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
    logger.info("Advertising as '%s' (service %s) for %ds...", name, SERVICE_UUID, duration)

    try:
        GenericWaitable().wait(duration, exception_on_timeout=False)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")

    counter_thread.stop()
    logger.info("Stop advertising")
    ble_device.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default=None, help="Dongle serial port (default: autodetect)")
    parser.add_argument("--name", default=None, help="Advertising name (default: TestDevice, "
                        "or PosCtrlBuds with --positive-control)")
    parser.add_argument("--duration", type=int, default=600, help="Duration in seconds (default: 600)")
    parser.add_argument("--positive-control", action="store_true",
                        help="Weak peripheral: advertise a Resolvable Private Address and accept "
                        "an unauthenticated Just Works bond (distributing IRK + identity). Use as "
                        "a positive control for the identity.extract disclosure check.")
    args = parser.parse_args()
    name = args.name or ("PosCtrlBuds" if args.positive_control else "TestDevice")
    main(args.port, name, args.duration, positive_control=args.positive_control)
