"""Generic central GATT client: connect to a device and explore its database.

The dongle acts as central: it looks for a target (by name or address), connects,
discovers services and characteristics and reads the readable ones. Meant to test
ANY device without knowing its services in advance.

Usage:
    python client.py --name "MyDevice"
    python client.py --address AA:BB:CC:DD:EE:FF
    python client.py --name "MyDevice" --subscribe   # keep listening for notifications
"""
from __future__ import annotations

import argparse
import struct

from blatann.bt_sig.uuids import UUID_DESCRIPTION_MAP
from blatann.examples import example_utils

from bledev import open_device

logger = example_utils.setup_logger(level="INFO")

# Common name (e.g. "Battery Level") for a SIG-assigned 16-bit UUID, keyed by int
_NAME_BY_U16 = {k.uuid: v for k, v in UUID_DESCRIPTION_MAP.items()}


def _u16(uuid) -> int | None:
    """The 16-bit value of a UUID (works for both Uuid16 and base-derived Uuid128)."""
    v = getattr(uuid, "uuid16", None)
    if isinstance(v, int):
        return v
    v = getattr(uuid, "uuid", None)
    return v if isinstance(v, int) else None


def name_for_uuid(uuid) -> str:
    """SIG common name for a UUID, or '' if unknown/custom."""
    name = UUID_DESCRIPTION_MAP.get(uuid)
    if name:
        return name
    u16 = _u16(uuid)
    return _NAME_BY_U16.get(u16, "") if u16 is not None else ""


def _appearance(value: int) -> str:
    try:
        from blatann.bt_sig.assigned_numbers import Appearance
        return Appearance(value).name.replace("_", " ").title()
    except Exception:
        return f"0x{value:04X}"


# 16-bit characteristics whose value is a UTF-8 string (Device Name + Device Info)
_STRING_U16 = {0x2A00, 0x2A23, 0x2A24, 0x2A25, 0x2A26, 0x2A27, 0x2A28, 0x2A29}


def _interpret(uuid, data: bytes) -> str | None:
    """Best-effort human interpretation of a characteristic value; None if unknown."""
    u16 = _u16(uuid)
    if u16 is None or not data:
        return None
    try:
        if u16 in _STRING_U16:
            return '"' + data.decode("utf-8", "replace") + '"'
        if u16 == 0x2A19:  # Battery Level
            return f"{data[0]}%"
        if u16 == 0x2A07:  # Tx Power Level
            return f"{struct.unpack('<b', data[:1])[0]} dBm"
        if u16 == 0x2A01 and len(data) >= 2:  # Appearance
            return _appearance(struct.unpack("<H", data[:2])[0])
        if u16 == 0x2A50 and len(data) >= 7:  # PnP ID
            _src, vid, pid, ver = struct.unpack("<BHHH", data[:7])
            return f"vendor=0x{vid:04X} product=0x{pid:04X} v{ver >> 8}.{(ver >> 4) & 0xF}.{ver & 0xF}"
        if u16 == 0x2A37 and len(data) >= 2:  # Heart Rate Measurement
            hr = struct.unpack("<H", data[1:3])[0] if data[0] & 1 else data[1]
            return f"{hr} bpm"
    except Exception:
        return None
    return None


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


def explore_peer(ble_device, target_address, do_subscribe=False):
    """Connect to target_address, discover services and read readable chars."""
    logger.info("Connecting to %s", target_address)
    peer = ble_device.connect(target_address).wait()
    if not peer:
        logger.error("Connection failed/timed out")
        return
    logger.info("Connected (conn_handle=%s)", peer.conn_handle)

    _, event_args = peer.discover_services().wait(10, exception_on_timeout=False)
    logger.info("Service discovery status: %s", event_args.status)

    subscribed = []
    for service in peer.database.services:
        logger.info("Service %s %s", service.uuid, name_for_uuid(service.uuid))
        for char in service.characteristics:
            line = f"  Char {char.uuid} {name_for_uuid(char.uuid)} [{_props(char)}]"
            if char.readable:
                _, read_args = char.read().wait(5, exception_on_timeout=False)
                if read_args is not None:
                    line += f" = {_format_value(read_args.value)}"
                    interp = _interpret(char.uuid, read_args.value)
                    if interp:
                        line += f" → {interp}"
            logger.info(line)
            if do_subscribe and char.subscribable:
                char.subscribe(_on_notification).wait(5, exception_on_timeout=False)
                subscribed.append(char)

    if do_subscribe and subscribed:
        logger.info("Listening for notifications for 30s (Ctrl-C to exit)...")
        try:
            from blatann.waitables import GenericWaitable
            GenericWaitable().wait(30, exception_on_timeout=False)
        except KeyboardInterrupt:
            pass

    logger.info("Disconnecting")
    peer.disconnect().wait()


def connect_and_explore(target_address, port=None, do_subscribe=False):
    """Open the dongle, explore target_address (a BLEGapAddr or PeerAddress), close."""
    ble_device = open_device(port)
    try:
        explore_peer(ble_device, target_address, do_subscribe)
    finally:
        ble_device.close()


# --- Interactive GATT browser ----------------------------------------------

def _props(char) -> str:
    flags = []
    if char.readable:
        flags.append("R")
    if char.writable or char.writable_without_response:
        flags.append("W")
    if char.subscribable:
        flags.append("N")
    return "/".join(flags) or "-"


def _parse_write_value(text: str) -> bytes:
    """Parse a write value: 'hex:0a0b', 'text:hello', or auto (hex if it looks
    like hex, else UTF-8 text)."""
    text = text.strip()
    low = text.lower()
    if low.startswith("hex:"):
        return bytes.fromhex(text[4:].strip().replace(" ", ""))
    if low.startswith("text:"):
        return text[5:].encode()
    compact = text.replace(" ", "")
    if compact and len(compact) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in compact):
        return bytes.fromhex(compact)
    return text.encode()


# --- GATT primitives reused by the interactive menu tree -------------------

def connect_and_discover(ble_device, target_address):
    """Connect and run service discovery; return the peer, or None on failure."""
    print(f"Connecting to {target_address} ...")
    peer = ble_device.connect(target_address).wait()
    if not peer:
        print("  Connection failed/timed out.")
        return None
    print("  Connected. Discovering services...")
    peer.discover_services().wait(10, exception_on_timeout=False)
    return peer


def char_list(peer):
    """Flat list of (service, characteristic) for the whole GATT database."""
    return [(svc, ch) for svc in peer.database.services for ch in svc.characteristics]


def char_label(svc, char, cached=None) -> str:
    """One-line label for a characteristic (props, uuid, name, service, cached read)."""
    cname = name_for_uuid(char.uuid)
    sname = name_for_uuid(svc.uuid) or str(svc.uuid)
    label = f"[{_props(char):5}] {char.uuid}" + (f" {cname}" if cname else "") + f"  (svc {sname})"
    if cached:
        label += f"  = {cached}"
    return label


def read_char(char):
    """Read a characteristic; print and return the display string (or None)."""
    if not char.readable:
        print("  Not readable.")
        return None
    _, event_args = char.read().wait(5, exception_on_timeout=False)
    if event_args is None:
        print("  Read failed/timed out.")
        return None
    disp = _format_value(event_args.value)
    interp = _interpret(char.uuid, event_args.value)
    if interp:
        disp += f"   → {interp}"
    print(f"  Read: {disp}")
    return disp


def write_char(char):
    """Prompt for a value and write it to the characteristic."""
    if not (char.writable or char.writable_without_response):
        print("  Not writable.")
        return
    try:
        data = _parse_write_value(input("  Value (hex:.. / text:.. / auto): "))
    except ValueError:
        print("  Invalid hex value.")
        return
    result = char.write(data).wait(5, exception_on_timeout=False)
    print(f"  Wrote {data.hex(' ')} ({len(data)} bytes)" if result else "  Write failed/timed out.")


def read_all(pairs):
    """Read every readable characteristic in (service, char) pairs, interpreted."""
    read_any = False
    for _svc, char in pairs:
        if not char.readable:
            continue
        read_any = True
        name = name_for_uuid(char.uuid) or str(char.uuid)
        _, event_args = char.read().wait(5, exception_on_timeout=False)
        if event_args is None:
            print(f"  {name}: (read failed/timed out)")
            continue
        disp = _format_value(event_args.value)
        interp = _interpret(char.uuid, event_args.value)
        print(f"  {name}: {disp}" + (f"   → {interp}" if interp else ""))
    if not read_any:
        print("  No readable characteristics.")


_NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"   # write
_NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"   # notify


def serial_console(peer):
    """Interactive Nordic UART console: subscribe TX, send lines to RX."""
    rx = tx = None
    for svc in peer.database.services:
        for char in svc.characteristics:
            u = str(char.uuid).lower()
            if u == _NUS_RX:
                rx = char
            elif u == _NUS_TX:
                tx = char
    if rx is None and tx is None:
        print("  Nordic UART characteristics not found.")
        return

    if tx is not None and tx.subscribable:
        tx.subscribe(lambda c, e: print(f"  << {e.value.decode('utf-8', 'replace')}")).wait(
            5, exception_on_timeout=False)
    print("  Serial console — type text to send (hex:.. / text:.. / auto), blank line to exit.")
    try:
        while True:
            line = input("  >> ")
            if line == "":
                break
            try:
                data = _parse_write_value(line)
            except ValueError:
                print("  Invalid hex value.")
                continue
            if rx is not None:
                rx.write(data).wait(5, exception_on_timeout=False)
            else:
                print("  No writable RX characteristic.")
    except (EOFError, KeyboardInterrupt):
        pass
    if tx is not None:
        try:
            tx.unsubscribe()
        except Exception:
            pass


def subscribe_char(char, seconds=15):
    """Subscribe and print notifications for a while, then unsubscribe."""
    if not char.subscribable:
        print("  Not subscribable.")
        return

    def _on_notify(c, e):
        print(f"    NOTIFY {c.uuid}: {_format_value(e.value)}")

    char.subscribe(_on_notify).wait(5, exception_on_timeout=False)
    print(f"  Subscribed — listening {seconds}s (Ctrl-C to stop)...")
    try:
        from blatann.waitables import GenericWaitable
        GenericWaitable().wait(seconds, exception_on_timeout=False)
    except KeyboardInterrupt:
        pass
    try:
        char.unsubscribe()
    except Exception:
        pass


def main(port, name, address, timeout, do_subscribe):
    ble_device = open_device(port)
    try:
        if address:
            from blatann.gap.gap_types import PeerAddress
            target_address = PeerAddress.from_string(address)
        else:
            ble_device.scanner.set_default_scan_params(timeout_seconds=timeout)
            logger.info("Scanning for '%s'...", name)
            target_address = example_utils.find_target_device(ble_device, name)
            if not target_address:
                logger.error("Target device '%s' not found", name)
                return
        explore_peer(ble_device, target_address, do_subscribe)
    finally:
        ble_device.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default=None, help="Dongle serial port (default: autodetect)")
    parser.add_argument("--name", default=None, help="Advertising name of the target device")
    parser.add_argument("--address", default=None, help="Target BLE address (alternative to --name)")
    parser.add_argument("--timeout", type=int, default=6, help="Scan timeout in seconds")
    parser.add_argument("--subscribe", action="store_true", help="Subscribe to notifiable characteristics")
    args = parser.parse_args()
    if not args.name and not args.address:
        parser.error("Provide --name or --address")
    main(args.port, args.name, args.address, args.timeout, args.subscribe)
