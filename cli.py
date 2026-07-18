"""Interactive CLI for the BLE bench.

Starts up, shows a menu of activities and runs one of your choice. When the
activity finishes it returns to the menu, until you quit.

Usage:
    python cli.py
"""
from __future__ import annotations

import subprocess
import sys

import client
import exposure
import livetable
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

def _device_action(title, options, default) -> str:
    """Render a one-action-per-line menu and return the chosen key.

    options is a list of (key, label). To add an action, add one entry here and
    one branch in the caller — keeps the growing menu readable.
    """
    print(title)
    for key, label in options:
        print(f"  {key}) {label}")
    choice = _prompt("Action", default).strip().lower()
    keys = {k for k, _ in options}
    return choice if choice in keys else default


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
    """Act on a BLE device picked from the scan table (no MAC copy-paste)."""
    print(f"\nSelected BLE device: {rec.address}  {rec.name or '(no name)'}"
          f"  [{rec.protocol or rec.manufacturer or '-'}]")
    action = _device_action("Actions:", [
        ("f", "Fast Pair GATT exposure check (passive)"),
        ("c", "Connect & browse GATT (read/write/subscribe)"),
        ("b", "Back"),
    ], default="f")
    if action == "f":
        print()
        print(exposure.report(rec))  # passive: reads collected advertising only
    elif action == "c":
        if not rec.connectable:
            print("  Note: not advertised as connectable — the connection may fail.")
        try:
            # Reuse the scanner's already-open dongle (avoids a close/reopen).
            client.interactive_gatt(scanner.ble_device, rec.peer)
        except Exception as err:  # keep the CLI alive on connection errors
            print(f"  Interaction error: {err}")
    input("\nPress Enter to return to the scan...")


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
    """Act on a Classic device picked from the scan table."""
    print(f"\nSelected Classic device: {rec.address}  {rec.name or '(no name)'}"
          f"  [{rec.cod_label or '-'}]")
    action = _device_action("Actions:", [
        ("i", "Show device details (bluetoothctl info)"),
        ("b", "Back"),
    ], default="i")
    if action == "i":
        subprocess.run(["bluetoothctl", "info", rec.address])
    input("\nPress Enter to return to the scan...")


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
