"""GATT enumeration over the host BlueZ adapter (hci0) via bluetoothctl.

A second, independent GATT discovery path for devices the nRF52/blatann cannot fully
enumerate. blatann's characteristic discovery wedges on some peripherals (see
`webui/blatann_patch.py`): it finds the services but not their characteristics. BlueZ
uses a completely different discovery implementation and often enumerates those vendor
services in full, so this is the fallback the webui offers from the "Could not enumerate
all GATT" warning.

It drives `bluetoothctl` the same way `scan_classic`/`scan_le` do (a scripted stdin
session, ANSI stripped from stdout) rather than D-Bus, to stay in the venv without a
dbus dependency. It runs on **hci0**, independent of the dongle, so it does not touch an
active dongle scan.

`enumerate(address)` returns ``{"gatt": [service...], "error": str}`` where each service
is ``{uuid, name, characteristics: [{uuid, name, props, value_hex, value_interp}]}`` —
the same shape `store.save_gatt` / the GATT templates expect. Characteristic *properties*
and *values* are not read here (list-attributes does not expose flags); this path exists
to recover the missing **structure** (service → characteristic UUIDs).
"""
from __future__ import annotations

import re
import subprocess

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_ATTR_HEADER = re.compile(r"^(Primary Service|Secondary Service|Characteristic|Descriptor)\b")
# A D-Bus object path for a GATT attribute under the device
_PATH = re.compile(r"^/org/bluez/\S+")


def _uuid16_short(uuid: str) -> str:
    """Return the 0xXXXX short form for a SIG base UUID, else the full UUID lowercased."""
    u = uuid.lower()
    if u.endswith("-0000-1000-8000-00805f9b34fb") and u.startswith("0000"):
        return f"0x{u[4:8].upper()}"
    return u


def _build_script(address: str, scan_seconds: int) -> str:
    """A bash driver that feeds bluetoothctl with real timing.

    `bluetoothctl` has **no** `sleep` command, so (like `scan_classic.pair`) the delays
    live in bash and `printf` feeds the commands: scan briefly to cache the device
    (otherwise `connect` reports "not available"), connect, wait for ServicesResolved,
    list the GATT, disconnect.
    """
    return (
        "sleep 1; printf 'power on\\nscan on\\n'; "
        f"sleep {scan_seconds}; printf 'scan off\\n'; sleep 1; "
        f"printf 'connect {address}\\n'; sleep 11; "
        f"printf 'gatt.list-attributes {address}\\n'; sleep 3; "
        f"printf 'disconnect {address}\\nquit\\n'; sleep 1"
    )


def _parse_list_attributes(text: str, address: str) -> list[dict]:
    """Parse `gatt.list-attributes` output into a service->characteristic tree.

    Each attribute is a header line ('Primary Service'/'Characteristic'/'Descriptor')
    followed by indented lines carrying its object path, UUID and friendly name. The
    object-path hierarchy (``serviceNNNN/charNNNN/descNNNN``) gives the nesting, so we
    group characteristics under the service that prefixes their path.
    """
    # Collect raw attributes: (kind, path, uuid, name)
    attrs: list[tuple[str, str, str, str]] = []
    kind = path = uuid = name = ""

    def flush():
        nonlocal kind, path, uuid, name
        if kind and path:
            attrs.append((kind, path, uuid, name))
        kind = path = uuid = name = ""

    for raw in text.splitlines():
        line = _ANSI.sub("", raw).replace("\r", "").strip()
        if not line:
            continue
        m = _ATTR_HEADER.match(line)
        if m:
            flush()
            kind = m.group(1)
            continue
        if not kind:
            continue
        if _PATH.match(line):
            path = line.split()[0]
        elif _UUID.match(line):
            uuid = line
        else:
            name = line
    flush()

    services: dict[str, dict] = {}
    chars: dict[str, dict] = {}
    # First pass: services (so characteristics can attach regardless of order).
    for kind, p, uuid, name in attrs:
        last = p.rsplit("/", 1)[-1]
        if last.startswith("service"):
            services[p] = {"uuid": _uuid16_short(uuid), "name": name, "characteristics": []}
    # Second pass: characteristics under their parent service.
    for kind, p, uuid, name in attrs:
        last = p.rsplit("/", 1)[-1]
        if last.startswith("char"):
            svc_path = p.rsplit("/", 1)[0]
            node = services.get(svc_path)
            if node is not None:
                ch = {"uuid": _uuid16_short(uuid), "name": name,
                      "props": "", "value_hex": "", "value_interp": ""}
                node["characteristics"].append(ch)
                chars[p] = ch
    return list(services.values())


_HCI_DOWN_HINT = ("hci0 is down or has no default controller — bring the host adapter "
                  "up first: `sudo systemctl start bluetooth && sudo hciconfig hci0 up`.")


def enumerate(address: str, scan_seconds: int = 4, timeout: int = 60) -> dict:
    """Connect over hci0 and enumerate the device's GATT. Returns {"gatt", "error"}."""
    address = address.split(",")[0].strip().upper()
    script = _build_script(address, scan_seconds)
    try:
        res = subprocess.run(["bash", "-c", f"({script}) | bluetoothctl"],
                             capture_output=True, text=True, timeout=timeout)
        out = _ANSI.sub("", (res.stdout or "") + (res.stderr or ""))
    except subprocess.TimeoutExpired as err:
        out = err.stdout if isinstance(err.stdout, str) else ""
    except OSError as err:
        return {"gatt": [], "error": f"bluetoothctl not available: {err}"}

    low = out.lower()
    tree = _parse_list_attributes(out, address)
    if not tree:
        # No controller / adapter powered off is the most common blocker on this bench.
        if ("no default controller" in low or "failed to set power on" in low
                or "controller is not available" in low or "no default adapter" in low):
            return {"gatt": [], "error": _HCI_DOWN_HINT}
        if "failed to connect" in low or "not available" in low or "br-connection" in low:
            return {"gatt": [], "error": "BlueZ could not connect to the device "
                    "(not in range / not connectable now). If hci0 is down: " + _HCI_DOWN_HINT}
        return {"gatt": [], "error": "BlueZ connected but listed no GATT attributes "
                "(the device may require pairing, or hci0 could not be powered — " + _HCI_DOWN_HINT + ")"}
    return {"gatt": tree, "error": ""}


if __name__ == "__main__":
    import json
    import sys
    addr = sys.argv[1] if len(sys.argv) > 1 else None
    if not addr:
        raise SystemExit("usage: python gatt_bluez.py <ADDRESS>")
    print(json.dumps(enumerate(addr), indent=2))
