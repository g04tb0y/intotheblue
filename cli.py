"""Interactive CLI for the BLE bench.

Starts up, shows a menu of activities and runs one of your choice. When the
activity finishes it returns to the menu, until you quit.

Usage:
    python cli.py
"""
from __future__ import annotations

import subprocess
import sys

import capabilities
import client
import exposure
import livetable
import menu
import scan_classic
import scan_live


def _prompt(message: str, default: str | None = None) -> str:
    """input() with a default value (Enter) and EOF/Ctrl-D handling."""
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{message}{suffix}: ").strip()
    except EOFError:
        return default or ""
    return answer or (default or "")


def _prompt_int(message: str, default: int) -> int:
    while True:
        raw = _prompt(message, str(default))
        try:
            return int(raw)
        except ValueError:
            print(f"  Invalid value: '{raw}'. Please enter an integer.")


# --- Activities -------------------------------------------------------------

def _scan_interact_loop(scanner, columns, title, interact_fn) -> None:
    """Live table loop: select a device -> act on it -> back to the same scan.

    The scanner (and, for BLE, its open dongle) stays alive across interactions,
    so we pause scanning, act, then resume — no close/reopen of the device.
    Q in the table returns None and exits this loop back to the main menu.
    """
    while True:
        selected = livetable.run(scanner, columns, title)
        if selected is None:
            return
        scanner.pause()
        try:
            interact_fn(scanner, selected)
        finally:
            scanner.resume()


def activity_scan() -> None:
    """Live BLE scan: continuously updating table, CSV export, GATT interaction."""
    port = _prompt("Dongle port (Enter = autodetect)") or None
    print("\nOpening the dongle...")
    scanner = scan_live.LiveScanner(port)
    scanner.open()
    scanner.start()
    try:
        _scan_interact_loop(scanner, scan_live.COLUMNS, "BLE", _interact_ble)
    finally:
        scanner.close()


def _interact_ble(scanner, rec) -> None:
    """Enter the per-device menu tree for a BLE device picked from the scan."""
    menu.run(_ble_device_menu(scanner, rec))


def _leaf(fn):
    """Wrap a print-only action so it runs and then stays on the current node."""
    def action():
        try:
            fn()
        except Exception as err:  # keep the tree alive on runtime/connection errors
            print(f"  Error: {err}")
        return None
    return action


def _ble_device_menu(scanner, rec):
    title = f"BLE {rec.address} {rec.name or ''}".rstrip()

    def build():
        return [
            ("f", "Fast Pair GATT exposure check (passive)",
             _leaf(lambda: print("\n" + exposure.report(rec)))),
            ("k", "Capabilities (connect: inspect & interact)",
             lambda: _capabilities_menu(scanner, rec)),
            ("g", "Browse GATT (connect)", lambda: _gatt_menu(scanner, rec)),
        ]
    return menu.Menu(title, build)


def _gatt_menu(scanner, rec):
    """GATT node: connects on enter, disconnects on leave, lists characteristics."""
    state = {"peer": None, "values": {}}

    def on_enter():
        if not rec.connectable:
            print("  Note: not advertised as connectable — the connection may fail.")
        try:
            peer = client.connect_and_discover(scanner.ble_device, rec.peer)
        except Exception as err:
            print(f"  Connection error: {err}")
            return False
        if peer is None:
            return False
        state["peer"] = peer
        return True

    def on_leave():
        if state["peer"]:
            print("  Disconnecting.")
            try:
                state["peer"].disconnect().wait()
            except Exception:
                pass

    def build():
        items = []
        for i, (svc, ch) in enumerate(client.char_list(state["peer"]), 1):
            label = client.char_label(svc, ch, state["values"].get(i))
            items.append((str(i), label, _char_menu_factory(ch, state["values"], i)))
        return items

    return menu.Menu("GATT", build, on_enter=on_enter, on_leave=on_leave)


def _capabilities_menu(scanner, rec):
    """Capabilities node: connect, list detected capabilities as sub-nodes."""
    state = {"peer": None, "detected": []}

    def on_enter():
        if not rec.connectable:
            print("  Note: not advertised as connectable — the connection may fail.")
        try:
            peer = client.connect_and_discover(scanner.ble_device, rec.peer)
        except Exception as err:
            print(f"  Connection error: {err}")
            return False
        if peer is None:
            return False
        state["peer"] = peer
        state["detected"], _flags, _ns, _nc = capabilities.analyze(peer.database.services)
        return True

    def on_leave():
        if state["peer"]:
            print("  Disconnecting.")
            try:
                state["peer"].disconnect().wait()
            except Exception:
                pass

    def build():
        items = [("r", "Show full capability report",
                  _leaf(lambda: print("\n" + capabilities.report(state["peer"].database.services))))]
        for i, (label, _note) in enumerate(state["detected"], 1):
            items.append((str(i), label, _capability_opener(state["peer"], label)))
        return items

    return menu.Menu(f"Capabilities {rec.address}", build, on_enter=on_enter, on_leave=on_leave)


def _capability_opener(peer, label):
    """Open a single capability's node: Inspect (read-only) vs Interact (active)."""
    def open_cap():
        svcs = capabilities.match_services(peer.database.services, label)
        pairs = [(s, c) for s in svcs for c in s.characteristics]
        is_nus = any(str(s.uuid).lower() == "6e400001-b5a3-f393-e0a9-e50e24dcca9e" for s in svcs)
        has_active = is_nus or any(c.writable or c.writable_without_response or c.subscribable
                                   for _s, c in pairs)

        def build():
            children = [("i", "Inspect (read-only)", _inspect_opener(label, svcs, pairs))]
            if has_active:
                children.append(("x", "Interact (active)", _interact_opener(peer, pairs, is_nus)))
            return children
        return menu.Menu(label, build)
    return open_cap


def _inspect_opener(label, svcs, pairs):
    def open_inspect():
        def build():
            items = [("a", "Read all readable characteristics", _leaf(lambda: client.read_all(pairs)))]
            if label == "Firmware update (DFU/OTA)":
                items.append(("d", "DFU exposure verification (passive)",
                              _leaf(lambda: print("\n" + capabilities.dfu_exposure(svcs)))))
            for i, (svc, ch) in enumerate(pairs, 1):
                if ch.readable:
                    lbl = f"Read {ch.uuid} {client.name_for_uuid(ch.uuid)}".rstrip()
                    items.append((str(i), lbl, _leaf(lambda ch=ch: client.read_char(ch))))
            return items
        return menu.Menu("Inspect", build)
    return open_inspect


def _interact_opener(peer, pairs, is_nus):
    def open_interact():
        def build():
            items = []
            if is_nus:
                items.append(("c", "Serial console (send/receive)",
                              _leaf(lambda: client.serial_console(peer))))
            for i, (svc, ch) in enumerate(pairs, 1):
                name = client.name_for_uuid(ch.uuid)
                if ch.writable or ch.writable_without_response:
                    items.append((f"w{i}", f"Write {ch.uuid} {name}".rstrip(),
                                  _leaf(lambda ch=ch: client.write_char(ch))))
                if ch.subscribable:
                    items.append((f"s{i}", f"Subscribe {ch.uuid} {name}".rstrip(),
                                  _leaf(lambda ch=ch: client.subscribe_char(ch))))
            return items
        return menu.Menu("Interact", build)
    return open_interact


def _read_into(char, values, idx):
    disp = client.read_char(char)
    if disp is not None:
        values[idx] = disp


def _char_menu_factory(char, values, idx):
    def open_char():
        def build():
            items = []
            if char.readable:
                items.append(("r", "Read", _leaf(lambda: _read_into(char, values, idx))))
            if char.writable or char.writable_without_response:
                items.append(("w", "Write", _leaf(lambda: client.write_char(char))))
            if char.subscribable:
                items.append(("s", "Subscribe (15s)", _leaf(lambda: client.subscribe_char(char))))
            return items
        return menu.Menu(f"Char {char.uuid}", build)
    return open_char


def activity_classic() -> None:
    """Live Bluetooth Classic (BR/EDR) scan via the host adapter."""
    print()
    scanner = scan_classic.ClassicScanner()
    scanner.start()
    try:
        _scan_interact_loop(scanner, scan_classic.COLUMNS, "Classic", _interact_classic)
    finally:
        scanner.close()


def _interact_classic(scanner, rec) -> None:
    """Enter the per-device menu tree for a Classic device picked from the scan."""
    menu.run(_classic_device_menu(rec))


def _classic_profiles(rec):
    uuid16s, vendor = scan_classic.read_profiles(rec.address)
    print("\n" + capabilities.classic_report(uuid16s, vendor))


def _classic_device_menu(rec):
    title = f"Classic {rec.address} {rec.name or ''}".rstrip()

    def build():
        return [
            ("p", "Profile capability enumeration (SDP, detection-only)",
             _leaf(lambda: _classic_profiles(rec))),
            ("i", "Show raw device details (bluetoothctl info)",
             _leaf(lambda: subprocess.run(["bluetoothctl", "info", rec.address]))),
        ]
    return menu.Menu(title, build)


# Activity registry: key -> (label, handler).
# To add one: define the handler and register it here.
ACTIVITIES: dict[str, tuple[str, callable]] = {
    "scan": ("Live BLE scan (table + CSV)", activity_scan),
    "classic": ("Live Bluetooth Classic scan (BR/EDR)", activity_classic),
}


# --- Menu -------------------------------------------------------------------

def _print_menu() -> list[str]:
    print("\n=== BLE bench — choose an activity ===")
    keys = list(ACTIVITIES)
    for i, key in enumerate(keys, start=1):
        label, _ = ACTIVITIES[key]
        print(f"  {i}) {label}")
    print("  q) Quit")
    return keys


def main() -> None:
    while True:
        keys = _print_menu()
        choice = _prompt("Choice").lower()

        if choice in ("q", "quit", "exit"):
            print("Bye!")
            return

        # Accept both the number and the key name (e.g. "scan")
        selected = None
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(keys):
                selected = keys[idx]
        elif choice in ACTIVITIES:
            selected = choice

        if selected is None:
            print(f"  Invalid choice: '{choice}'")
            continue

        _, handler = ACTIVITIES[selected]
        try:
            handler()
        except KeyboardInterrupt:
            print("\n  Activity interrupted.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye!")
        sys.exit(0)
