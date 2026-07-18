#!/usr/bin/env python3
"""D-Bus helper: bounded PBAP PullAll via BlueZ obexd. Prints the vCards to stdout.

Kept separate and run with the SYSTEM python3 because it needs dbus-python, which
is not in the project venv. The `MaxCount` filter bounds the pull at the protocol
level, so only `count` entries ever leave the phone — not the whole phonebook.

Usage:  python3 pbap_pull.py <address> <count>
Requires the device to be already PAIRED (this never pairs).
"""
import os
import sys
import tempfile
import time

import dbus

_BUS = "org.bluez.obex"


def main() -> int:
    address = sys.argv[1]
    count = max(1, min(int(sys.argv[2]), 20))

    bus = dbus.SessionBus()
    client = dbus.Interface(bus.get_object(_BUS, "/org/bluez/obex"), "org.bluez.obex.Client1")
    session = client.CreateSession(address, {"Target": dbus.String("pbap")})
    try:
        pbap = dbus.Interface(bus.get_object(_BUS, session), "org.bluez.obex.PhonebookAccess1")
        pbap.Select("int", "pb")
        tmp = tempfile.mkdtemp(prefix="pbap_")
        target = os.path.join(tmp, "sample.vcf")
        _xfer, props = pbap.PullAll(target, {
            "Format": dbus.String("vcard30"),
            "MaxCount": dbus.UInt16(count),
        })
        xprops = dbus.Interface(bus.get_object(_BUS, _xfer), "org.freedesktop.DBus.Properties")
        status = ""
        for _ in range(80):
            try:
                status = str(xprops.Get("org.bluez.obex.Transfer1", "Status"))
            except dbus.DBusException:
                status = "complete"  # object gone == finished
                break
            if status in ("complete", "error"):
                break
            time.sleep(0.25)

        fname = str(props.get("Filename", target))
        data = ""
        for path in (fname, target):
            if os.path.exists(path) and os.path.getsize(path) > 0:
                with open(path, "r", errors="replace") as f:
                    data = f.read()
                break
        for path in (fname, target):
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass

        sys.stdout.write(data)
        sys.stderr.write(f"status={status}\n")
        return 0
    finally:
        try:
            client.RemoveSession(session)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
