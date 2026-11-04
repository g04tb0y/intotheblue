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
import identity
import livetable
import menu
import scan_classic
import scan_fused
import scan_le
import scan_live
import sniff


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


def activity_web() -> None:
    """Launch the web UI + MCP server (owns the dongle for its whole lifetime).

    Mutually exclusive with the other activities: only one process may open the
    dongle, so this blocks until you stop the server (Ctrl-C).
    """
    host = _prompt("Bind host", "127.0.0.1")
    port = _prompt_int("Port", 8000)
    print(f"\nStarting web+MCP on http://{host}:{port}  (MCP at /mcp). Ctrl-C to stop.")
    print("This owns the dongle — do not run the scan activities meanwhile.")
    import uvicorn  # lazy: avoids importing FastAPI for the other activities
    uvicorn.run("webui.app:app", host=host, port=port)


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


def _classic_device_menu(rec):
    title = f"Classic {rec.address} {rec.name or ''}".rstrip()

    def build():
        return [
            ("k", "Profiles & interactions (SDP)", lambda: _classic_profiles_menu(rec)),
            ("p", "Pair / bond this device (needed for active actions)",
             _leaf(lambda: print(scan_classic.pair(rec.address)))),
            ("i", "Show raw device details (bluetoothctl info)",
             _leaf(lambda: subprocess.run(["bluetoothctl", "info", rec.address]))),
        ]
    return menu.Menu(title, build)


def _classic_profiles_menu(rec):
    """Interactive SDP profiles: each detected profile is a node with its actions."""
    def build():
        uuid16s, vendor = scan_classic.read_profiles(rec.address)
        detected = capabilities.classic_detected(uuid16s)
        items = [("r", "Show full profile report",
                  _leaf(lambda: print("\n" + capabilities.classic_report(uuid16s, vendor))))]
        for i, (label, _note) in enumerate(detected, 1):
            items.append((str(i), label, _classic_profile_opener(rec, label)))
        if not detected:
            items.append(("d", "No SDP profiles cached — pair/connect the device first",
                          _leaf(lambda: None)))
        return items
    return menu.Menu(f"Profiles {rec.address}", build)


def _classic_profile_opener(rec, label):
    """A profile node: interactive actions for SPP/PBAP, info for the rest."""
    def open_profile():
        if label == "Phonebook (PBAP)":
            return _pbap_menu(rec)
        if label == "Serial Port (SPP)":
            return menu.Menu(label, lambda: [
                ("c", "Open serial console (RFCOMM, needs pairing)",
                 _leaf(lambda: __import__("spp").console(rec.address))),
            ])
        return menu.Menu(label, lambda: [
            ("d", "Description", _leaf(lambda: print(f"\n  {label}: {capabilities.classic_note(label)}"))),
        ])
    return open_profile


def _pbap_menu(rec):
    import pbap

    def build():
        return [
            ("p", "Access probe (no contacts, read-only check)",
             _leaf(lambda: print("\n" + pbap.probe(rec.address)))),
            ("x", "Sample pull (bounded active extraction, 5 entries)",
             _leaf(lambda: print("\n" + pbap.sample(rec.address, 5)))),
        ]
    return menu.Menu(f"PBAP {rec.address}", build)


def activity_le_bluez() -> None:
    """Live BLE scan fusing both radios — the dongle decides, BlueZ acts."""
    print()
    scanner = scan_fused.FusedScanner()
    scanner.open()
    if not scanner.dongle_ok:
        print(f"  Dongle unavailable ({scanner.dongle_error}) — continuing on BlueZ only;\n"
              f"  the Conn column will read '?' and targets cannot be pre-screened.")
    scanner.start()
    try:
        _scan_interact_loop(scanner, scan_fused.COLUMNS, "BLE fused", _interact_le_bluez)
    finally:
        scanner.close()


def _interact_le_bluez(scanner, rec) -> None:
    menu.run(_le_bluez_device_menu(scanner, rec))


def _le_bluez_device_menu(scanner, rec):
    title = f"BLE {rec.address} {rec.name or ''}".rstrip()
    # Two independent facts, so the label never overstates: Fast Pair advertising means
    # the device is in pairing mode (a target you can attribute by name and were meant
    # to act on), a resolvable address means the mechanism has something to unmask.
    note = scan_fused.target_note(rec)
    label = {
        "FP+RPA": "Identity address disclosure (active: connect) — READY: Fast Pair + RPA",
        "FP": "Identity address disclosure (active: connect) — Fast Pair, but no RPA to unmask",
        "RPA": "Identity address disclosure (active: connect) — RPA reachable, ownership UNVERIFIED",
    }.get(note, "Identity address disclosure (active) — not a target, will explain")

    def build():
        return [
            ("i", label, _leaf(lambda: _run_identity(scanner, rec))),
            ("f", "Exposure check (passive)",
             _leaf(lambda: print("\n" + exposure.report(rec)))),
            ("n", "Show raw device details (bluetoothctl info)",
             _leaf(lambda: subprocess.run(["bluetoothctl", "info", rec.address]))),
        ]
    return menu.Menu(title, build)


def _run_identity(scanner, rec) -> None:
    """Run the active identity-address disclosure check on the selected device.

    No extra confirmation: choosing this entry is the explicit action, and the private
    address rotates while you answer prompts. The check itself refuses, without
    transmitting, any target it can already tell is pointless.
    """
    print("\n  ACTIVE: opens an LE connection. The disclosure, if any, happens there —"
          "\n  a Just Works pairing is attempted only as a fallback. No GATT write.")
    if not scan_fused.is_fast_pair(rec):
        # Still no prompt: the menu entry is the explicit action and an RPA rotates
        # while you answer. But an RPA cannot be attributed to an owner by looking at
        # it, so say plainly that this one is unattributed.
        print("  NOTE: this device is not advertising Fast Pair, so nothing here proves"
              "\n  whose it is. Only act on a device you put in pairing mode yourself.")
    # BlueZ allows a single default agent: hand it over to the check's own session.
    scanner.release_agent()
    try:
        print("  Running — this takes up to a minute...", flush=True)
        print("\n" + identity.extract(rec.address, rec.addr_type, rec.name,
                                      connectable=rec.connectable))
    finally:
        scanner.restore_agent()


def activity_sniff() -> None:
    """Live BLE sniff via the CC2652P (Sonoff) running Sniffle firmware.

    Starts as an advertising scan. Selecting a device offers "Follow this device's next
    connection" (Phase 2), which retargets the *same* open dongle to CONN_FOLLOW on that
    device's current address — the scan→select→follow flow, so an RPA peer is chased by the
    address you just saw it use rather than a hand-typed MAC.
    """
    port = _prompt("Sonoff port (Enter = autodetect)") or None
    active = _prompt("Active scan (transmits SCAN_REQ to elicit names)? y/N").lower() \
        in ("y", "yes")
    print("\nOpening the Sniffle dongle...")
    scanner = sniff.SniffleScanner(port, active=active)
    scanner.open()
    scanner.start()
    try:
        _scan_interact_loop(scanner, sniff.COLUMNS, "Sniffle", _interact_sniff)
    finally:
        scanner.close()


def _interact_sniff(scanner, rec) -> None:
    """Per-device menu for a Sniffle-discovered device: passive detail + follow."""
    menu.run(_sniff_device_menu(scanner, rec))


def _follow_device(scanner, rec) -> None:
    """Retarget the open dongle to follow this device's next connection, then show the
    live connection table until the operator quits (Q) — after which we revert to the
    advertising scan. Capturing the relationship needs a *fresh* CONNECT_IND, so the
    operator must trigger the device's (re)connection while this is live."""
    try:
        targ_mac = sniff._parse_mac(rec.address)
    except ValueError as exc:
        print(f"  Cannot follow: {exc}")
        return
    print(f"\n  Following {rec.address} ({rec.name or 'unknown'}). Trigger its "
          f"(re)connection now (toggle Bluetooth / power-cycle it, or connect from the "
          f"phone). Press Q in the table to stop and return to the scan.")
    _prompt("  Enter to start following")
    scanner.reconfigure(follow=True, targ_mac=targ_mac)
    try:
        _scan_interact_loop(scanner, sniff.CONN_COLUMNS,
                            f"Following {rec.address}", _interact_sniff_conn)
    finally:
        # Back to advertising scan on the same dongle (the outer loop uses sniff.COLUMNS).
        scanner.reconfigure(follow=False)


def _interact_sniff_conn(scanner, rec) -> None:
    """Per-connection menu for a followed link (Phase 2: passive relationship detail)."""
    menu.run(_sniff_conn_menu(rec))


def _sniff_conn_menu(rec):
    title = f"Connection {rec.central} <-> {rec.peripheral}"

    def details() -> None:
        print(f"\n  Central (phone):    {rec.central} ({rec.central_type})")
        print(f"  Peripheral (device):{rec.peripheral} ({rec.peripheral_type})")
        print(f"  Device name:        {rec.peripheral_name or '-'}  (from its advertising)")
        print(f"  Access address:     0x{rec.aa:08X}")
        print(f"  Conn interval:      {rec.interval_ms:.1f} ms   Latency: {rec.latency}   "
              f"Timeout: {rec.timeout_ms:.0f} ms")
        print(f"  Hop: {rec.hop}   Used data channels: {rec.channels}")
        print(f"  Packets:            C->P {rec.pkt_central}   P->C {rec.pkt_peripheral}")
        print(f"  Encrypted:          {'yes (ATT payload is ciphertext)' if rec.encrypted else 'not observed'}")
        print(f"  State:              {'terminated' if rec.terminated else 'live'}")
        print(f"  RSSI:               {rec.rssi}")

    def build():
        return [
            ("d", "Show connection details (passive)", _leaf(details)),
        ]
    return menu.Menu(title, build)


def _sniff_device_menu(scanner, rec):
    title = f"Sniffle {rec.address} {rec.name or ''}".rstrip()

    def details() -> None:
        print(f"\n  Address:      {rec.address} ({rec.addr_type})")
        print(f"  Name:         {rec.name or '-'}")
        print(f"  RSSI:         {rec.rssi}   Channel: {rec.chan}   "
              f"PHY: {sniff._PHY_NAMES[rec.phy] if rec.phy < len(sniff._PHY_NAMES) else '?'}")
        print(f"  Connectable:  {'yes' if rec.connectable else 'no'}"
              f"{' (directed)' if rec.directed else ''}")
        print(f"  Manufacturer: {rec.manufacturer or '-'}")
        print(f"  Type:         {rec.protocol or '-'}")
        print(f"  Fast Pair:    {rec.fast_pair or '-'}")
        print(f"  Services:     {', '.join(sorted(rec.services)) or '-'}")
        print(f"  Packets:      {rec.count}")

    def build():
        opts = [
            ("d", "Show observed details (passive)", _leaf(details)),
            ("f", "Follow this device's next connection (Phase 2)",
             _leaf(lambda: _follow_device(scanner, rec))),
        ]
        return opts
    return menu.Menu(title, build)


# Activity registry: key -> (label, handler).
# To add one: define the handler and register it here.
ACTIVITIES: dict[str, tuple[str, callable]] = {
    "scan": ("Live BLE scan — nRF52 dongle (table + CSV)", activity_scan),
    "classic": ("Live Bluetooth Classic scan (BR/EDR) — host adapter", activity_classic),
    "le_bluez": ("Live BLE scan — fused dongle+BlueZ (identity disclosure)",
                 activity_le_bluez),
    "sniff": ("Live BLE sniff — CC2652P/Sonoff (Sniffle; passive by default)",
              activity_sniff),
    "webui": ("Web UI + MCP server (persists scans/GATT; owns the dongle)", activity_web),
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


def _run_activity(key: str) -> None:
    _, handler = ACTIVITIES[key]
    try:
        handler()
    except KeyboardInterrupt:
        print("\n  Activity interrupted.")


def main(argv: list[str] | None = None) -> None:
    # Allow launching one activity directly, e.g. `python cli.py webui`, so the web
    # UI is a startup option of intotheblue rather than a separate package to invoke.
    # `argv` is taken from the command line when standalone; intotheblue.py passes an
    # explicit list (e.g. `[]` to force the menu) so its own `--cli` flag, already
    # consumed, is not mistaken here for an activity name.
    args = sys.argv[1:] if argv is None else argv
    if args:
        key = args[0].lower()
        if key in ACTIVITIES:
            _run_activity(key)
            return
        print(f"  Unknown activity: '{key}'. Known: {', '.join(ACTIVITIES)}")
        return
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

        _run_activity(selected)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye!")
        sys.exit(0)
