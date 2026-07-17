"""Central GATT client generico: connette a un device e ne esplora il database.

Il dongle fa da central: cerca un target (per nome o indirizzo), si connette,
scopre servizi e caratteristiche e legge quelle leggibili. Pensato per collaudare
QUALUNQUE device senza conoscerne in anticipo i servizi.

Uso:
    python client.py --name "MyDevice"
    python client.py --address AA:BB:CC:DD:EE:FF
    python client.py --name "MyDevice" --subscribe   # resta in ascolto delle notifiche
"""
from __future__ import annotations

import argparse

from blatann.examples import example_utils

from bledev import open_device

logger = example_utils.setup_logger(level="INFO")


def _format_value(value: bytes) -> str:
    hex_repr = value.hex(" ")
    try:
        ascii_repr = value.decode("ascii")
        if ascii_repr.isprintable():
            return f"{hex_repr}  ('{ascii_repr}')"
    except UnicodeDecodeError:
        pass
    return hex_repr


def _on_notification(characteristic, event_args):
    logger.info("NOTIFY %s: %s", characteristic.uuid, _format_value(event_args.value))


def main(port, name, address, timeout, do_subscribe):
    ble_device = open_device(port)
    ble_device.scanner.set_default_scan_params(timeout_seconds=timeout)

    if address:
        from blatann.gap.gap_types import PeerAddress
        target_address = PeerAddress.from_string(address)
    else:
        logger.info("Scanning for '%s'...", name)
        target_address = example_utils.find_target_device(ble_device, name)
        if not target_address:
            logger.error("Target device '%s' non trovato", name)
            ble_device.close()
            return

    logger.info("Connecting to %s", target_address)
    peer = ble_device.connect(target_address).wait()
    if not peer:
        logger.error("Connessione fallita/timeout")
        ble_device.close()
        return
    logger.info("Connected (conn_handle=%s)", peer.conn_handle)

    _, event_args = peer.discover_services().wait(10, exception_on_timeout=False)
    logger.info("Service discovery status: %s", event_args.status)

    subscribed = []
    for service in peer.database.services:
        logger.info("Service %s", service.uuid)
        for char in service.characteristics:
            flags = []
            if char.readable:
                flags.append("R")
            if char.writable or char.writable_without_response:
                flags.append("W")
            if char.subscribable:
                flags.append("N")
            line = f"  Char {char.uuid} [{'/'.join(flags) or '-'}]"
            if char.readable:
                _, read_args = char.read().wait(5, exception_on_timeout=False)
                if read_args is not None:
                    line += f" = {_format_value(read_args.value)}"
            logger.info(line)
            if do_subscribe and char.subscribable:
                char.subscribe(_on_notification).wait(5, exception_on_timeout=False)
                subscribed.append(char)

    if do_subscribe and subscribed:
        logger.info("In ascolto delle notifiche per 30s (Ctrl-C per uscire)...")
        try:
            from blatann.waitables import GenericWaitable
            GenericWaitable().wait(30, exception_on_timeout=False)
        except KeyboardInterrupt:
            pass

    logger.info("Disconnecting")
    peer.disconnect().wait()
    ble_device.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default=None, help="Porta seriale del dongle (default: autorilevata)")
    parser.add_argument("--name", default=None, help="Nome advertising del device target")
    parser.add_argument("--address", default=None, help="Indirizzo BLE del target (alternativo a --name)")
    parser.add_argument("--timeout", type=int, default=6, help="Timeout scansione in secondi")
    parser.add_argument("--subscribe", action="store_true", help="Iscriviti alle caratteristiche notificabili")
    args = parser.parse_args()
    if not args.name and not args.address:
        parser.error("Specificare --name oppure --address")
    main(args.port, args.name, args.address, args.timeout, args.subscribe)
