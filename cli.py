"""Interactive CLI for the BLE bench.

Starts up, shows a menu of activities and runs one of your choice. When the
activity finishes it returns to the menu, until you quit.

Usage:
    python cli.py
"""
from __future__ import annotations

import sys

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

def activity_scan() -> None:
    """Live BLE scan: continuously updating table, CSV export."""
    port = _prompt("Dongle port (Enter = autodetect)") or None
    print()
    scan_live.run(port)


# Activity registry: key -> (label, handler).
# To add one: define the handler and register it here.
ACTIVITIES: dict[str, tuple[str, callable]] = {
    "scan": ("Live BLE scan (table + CSV)", activity_scan),
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
