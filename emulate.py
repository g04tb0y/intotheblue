"""Emulate a BLE device: the dongle acts as a peripheral (advertising + GATT server).

Exposes a custom service with three characteristics, useful to test apps or other
centrals (e.g. the nRF Connect app, or client.py on a second dongle):

  - RX  (write)          the central writes some bytes
  - TX  (notify)         the peripheral notifies back what it received (echo)
  - counter (read/notify) counter that increments every second

Usage:
    python emulate.py --name "TestDevice"
    python emulate.py --name "TestDevice" --duration 600
    python emulate.py --fast-pair FB9B72        # clone a real Fast Pair advertisement
    python emulate.py --fast-pair FB9B72 --fast-pair-gatt   # + a working FE2C GATT server
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
from blatann.uuid import Uuid16, Uuid128
from blatann.waitables import GenericWaitable

import fastpair
from bledev import open_device

logger = example_utils.setup_logger(level="INFO")

# Google Fast Pair Service UUID (16-bit), for the advertisement-clone mode.
_FAST_PAIR_UUID = 0xFE2C
# Where the emulated Provider writes its anti-spoofing public key so the Seeker side
# (webui/MCP `fastpair_handshake`) can load it — operator-supplied, offline posture.
_FP_PUBKEY_PATH = "csv/fastpair_provider_pub.hex"

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


def _add_fast_pair_gatt(ble_device, provider_priv):
    """Add a working Fast Pair Service (FE2C) GATT server: the emulated Provider side.

    Mirrors ``--positive-control`` for the Key-based Pairing handshake — both the Seeker
    (webui) and this Provider are ours, so the whole exchange is validatable end-to-end
    offline. The anti-spoofing **private** key stays here; its **public** half is written
    to ``_FP_PUBKEY_PATH`` (and logged) for the Seeker to load. Only Key-based Pairing
    (…1234) is answered; Passkey/Account Key are exposed but not yet driven (items 2-3
    cover the handshake, not the full pairing flow).
    """
    service = ble_device.database.add_service(Uuid16(_FAST_PAIR_UUID))
    kbp_char = service.add_characteristic(
        Uuid128(fastpair.KBP_UUID),
        GattsCharacteristicProperties(read=False, write=True, write_no_response=True,
                                      notify=True, max_length=80, variable_length=True),
        b"")
    service.add_characteristic(
        Uuid128(fastpair.PASSKEY_UUID),
        GattsCharacteristicProperties(read=False, write=True, notify=True,
                                      max_length=16, variable_length=True),
        b"")
    service.add_characteristic(
        Uuid128(fastpair.ACCOUNT_KEY_UUID),
        GattsCharacteristicProperties(read=False, write=True, max_length=16,
                                      variable_length=True),
        b"")

    provider_addr = str(ble_device.address)

    def _on_kbp_write(characteristic, event_args):
        data = bytes(event_args.value)
        logger.info("FP KBP <- %d bytes: %s", len(data), data.hex())
        if len(data) < 80:
            logger.warning("FP KBP: no appended Seeker public key (len=%d) — cannot run "
                           "anti-spoofing ECDH; ignoring (would need a stored account key)", len(data))
            return
        try:
            seeker_pub = data[16:80]
            key = fastpair.derive_key(provider_priv, seeker_pub)
            req = fastpair.parse_kbp_request(key, data)
        except Exception as err:                                # noqa: BLE001
            logger.warning("FP KBP: could not decrypt/parse request: %s", err)
            return
        if req["provider_addr"].upper() != provider_addr.split(",")[0].upper():
            logger.warning("FP KBP: request Provider address %s != ours %s (responding anyway)",
                           req["provider_addr"], provider_addr)
        resp = fastpair.build_kbp_response(key, provider_addr)
        characteristic.set_value(resp, notify_client=True)
        logger.info("FP KBP -> notified response %s", resp.hex())

    kbp_char.on_write.register(_on_kbp_write)

    pub_hex = fastpair.public_key_bytes(provider_priv).hex()
    try:
        import os
        os.makedirs("csv", exist_ok=True)
        with open(_FP_PUBKEY_PATH, "w") as fh:
            fh.write(pub_hex + "\n")
    except OSError as err:
        logger.warning("Could not write %s: %s", _FP_PUBKEY_PATH, err)
    logger.info("FAST PAIR GATT — Provider anti-spoofing PUBLIC key (give this to the "
                "Seeker's handshake):\n  %s\n  (also saved to %s)", pub_hex, _FP_PUBKEY_PATH)
    return service


def _fast_pair_adv_data(model_id: bytes):
    """Clone a real Fast Pair *discoverable* advertisement.

    Real Fast Pair advertising is Service Data (AD type 0x16) under UUID 0xFE2C
    whose value is the device's 3-byte Model ID, sent connectable. Re-emitting the
    exact captured Model ID makes a seeker (an Android phone) recognise it as that
    product and offer to pair it — exercising the seeker without the device.

    The advertisement carries **no local name**, like a real Fast Pair device: the
    seeker resolves the product name from the Model ID via Google's registry, so a
    name in the frame would only mask that path. This clones only the
    *advertisement* — the Fast Pair GATT procedure (anti-spoofing key exchange) is
    not implemented, so a pairing attempt will not complete. Note a seeker only
    shows the pairing half-sheet for a model it does **not** already own.
    """
    # Service Data value = 16-bit UUID (little-endian) + Model ID
    service_data = bytes([_FAST_PAIR_UUID & 0xFF, _FAST_PAIR_UUID >> 8]) + model_id
    adv_data = advertising.AdvertisingData(
        flags=0x06, service_uuid16s=[Uuid16(_FAST_PAIR_UUID)], service_data=service_data)
    scan_data = advertising.AdvertisingData()   # nameless, like a real FP device
    return adv_data, scan_data


def main(port, name, duration, positive_control=False, fast_pair_model=None,
         fast_pair_gatt=False):
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

    if fast_pair_gatt:
        # Emulated Fast Pair Provider: generate the anti-spoofing key pair and stand up
        # the FE2C GATT server that answers Key-based Pairing (items 2-3 positive control).
        provider_priv = fastpair.gen_keypair()
        _add_fast_pair_gatt(ble_device, provider_priv)

    if fast_pair_model is not None:
        adv_data, scan_data = _fast_pair_adv_data(fast_pair_model)
        logger.info("FAST PAIR CLONE — advertising model id %s (FE2C, connectable, nameless)",
                    fast_pair_model.hex().upper())
    elif fast_pair_gatt:
        # No Model ID given but a FP GATT server is up: advertise FE2C connectable and named
        # so the Seeker can find and connect to it (a real Provider is nameless, but a named
        # beacon makes the bench target easy to select).
        adv_data = advertising.AdvertisingData(
            local_name=name, flags=0x06, service_uuid16s=[Uuid16(_FAST_PAIR_UUID)])
        scan_data = advertising.AdvertisingData()
    else:
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
    parser.add_argument("--fast-pair", metavar="MODELID", default=None,
                        help="Clone a real Fast Pair advertisement: emit FE2C service data with "
                        "this 3-byte (6 hex) Model ID, connectable, so a seeker/phone recognises "
                        "the product. E.g. --fast-pair FB9B72. Advertisement only (no FP GATT).")
    parser.add_argument("--fast-pair-gatt", action="store_true",
                        help="Stand up a working Fast Pair Service (FE2C) GATT server that answers "
                        "the Key-based Pairing handshake — the in-house Provider positive control "
                        "for the Seeker-side handshake/stress. Generates an anti-spoofing key pair "
                        "and prints/saves its public key. Combinable with --fast-pair MODELID.")
    args = parser.parse_args()

    fast_pair_model = None
    if args.fast_pair is not None:
        if args.positive_control:
            parser.error("--fast-pair and --positive-control are mutually exclusive")
        try:
            fast_pair_model = bytes.fromhex(args.fast_pair.removeprefix("0x").removeprefix("0X"))
        except ValueError:
            parser.error(f"--fast-pair: '{args.fast_pair}' is not valid hex")
        if len(fast_pair_model) != 3:
            parser.error("--fast-pair: Model ID must be exactly 3 bytes (6 hex digits)")
    if args.fast_pair_gatt and args.positive_control:
        parser.error("--fast-pair-gatt and --positive-control are mutually exclusive")

    if args.name:
        name = args.name
    elif args.positive_control:
        name = "PosCtrlBuds"
    elif fast_pair_model is not None:
        name = "FPClone"
    elif args.fast_pair_gatt:
        name = "FPProvider"
    else:
        name = "TestDevice"
    main(args.port, name, args.duration, positive_control=args.positive_control,
         fast_pair_model=fast_pair_model, fast_pair_gatt=args.fast_pair_gatt)
