"""Identity address (BDADDR) disclosure check — ACTIVE, host-side.

A device advertising with a Resolvable Private Address is supposed to keep its
permanent Identity Address secret from anyone without its IRK. This check tests
whether that holds in practice: it opens a plain BLE connection from the host
BlueZ stack and attempts a Just Works pairing, then looks for a *second*,
different address appearing in BlueZ's own device table — the identity the peer
distributed during SMP.

Why it is interesting: the disclosure happens in the **host's** device table, not
in the accessory's application layer. No GATT characteristic is written; the Fast
Pair service check below is advisory and never gates the flow. An accessory
patched against CVE-2025-36911 (a pairing-mode check on the Fast Pair Key-Based
Pairing characteristic) is therefore not protected by that fix here.

Runs on the host adapter (`bluetoothctl`, hci0) — never on the nRF52 dongle, and
only on a target already in BlueZ's cache, which is why it is reachable from the
`scan_le` table. ACTIVE: it transmits, and it may leave a bond on the target.
Gate it behind an explicit user action.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time

import exposure
# Same output shapes as the Classic scanner: reuse its parsing rather than
# maintaining a second copy of these regexes.
from scan_classic import _ANSI, _INFO_HEADER, _PROMPT

# A row of `devices` output: "Device AA:BB:CC:DD:EE:FF Name"
_DEVICES_ROW = re.compile(r"^Device ([0-9A-F:]{17})(?:\s+(.*))?$")
# Any address mentioned in an event/report line
_ANY_ADDR = re.compile(r"([0-9A-F:]{17})")
# Lines that mean *our* pairing progressed, as opposed to ambient discovery noise
# ([NEW]/[CHG] RSSI events for unrelated neighbours arrive constantly while scanning).
_PAIR_STATE = re.compile(r"(Paired|Bonded):\s*yes|Pairing successful", re.I)
# RSSI inside an event line: "RSSI: 0xffffffcc (-52)"
_RSSI = re.compile(r"RSSI:.*\((-?\d+)\)")

# Address kinds with no private address to unmask: the check refuses them up front,
# before transmitting anything. A *static* random address is a permanent identity
# address in its own right (Core spec Vol 3 Part C 10.8) — it is not a privacy feature.
_NO_PRIVATE_ADDRESS = {
    "pub": ("public", ["the device already advertises its permanent",
                       "identity address in the clear."]),
    "rnd-s": ("random static", ["a static random address IS a permanent identity",
                                "address: it does not rotate (only a reboot may change",
                                "it), so there is no private address to unmask."]),
}

_FOOTER_CONNECTED = (
    "ACTIVE test — a BLE connection was opened and a Just Works pairing was attempted. "
    "No GATT characteristic was written. A bond may remain on the target device itself "
    "and has to be cleared there.")
_FOOTER_CONNECT_ONLY = (
    "ACTIVE test — a BLE connection was opened; no pairing was attempted because the "
    "connection alone disclosed the address. No GATT characteristic was written and no "
    "bond can have been created, so nothing remains on the target device.")
_FOOTER_NOT_CONNECTED = (
    "ACTIVE test — a connection to the target was attempted and did not succeed. "
    "No GATT characteristic was written and no bond can have been created.")


class _Session:
    """One interactive `bluetoothctl` process, driven step by step.

    `scan_classic.pair` runs a canned script and parses the result at the end; this
    check instead has to inspect the output *between* steps — the device table before
    and after the pairing attempt is exactly what reveals the leak.
    """

    def __init__(self):
        self._proc = subprocess.Popen(
            ["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1)
        self._lines: list[str] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._read_loop, daemon=True).start()
        time.sleep(1.0)  # let it connect to bluetoothd and settle

    def _read_loop(self) -> None:
        if not self._proc.stdout:
            return
        for raw in self._proc.stdout:
            line = _PROMPT.sub("", _ANSI.sub("", raw).replace("\r", "")).strip()
            with self._lock:
                self._lines.append(line)

    def send(self, cmd: str, wait: float = 1.5, done: tuple[str, ...] = ()) -> list[str]:
        """Send a command; return the lines it produced.

        Waits up to `wait` seconds, returning early once a line contains one of the
        `done` markers (so a fast failure doesn't cost the whole budget).
        """
        with self._lock:
            start = len(self._lines)
        if not self._proc.stdin:
            return []
        try:
            self._proc.stdin.write(cmd + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            return []
        deadline = time.time() + wait
        while time.time() < deadline:
            time.sleep(0.2)
            with self._lock:
                chunk = self._lines[start:]
            if done and any(d.lower() in line.lower() for line in chunk for d in done):
                break
        with self._lock:
            return self._lines[start:]

    def close(self) -> None:
        try:
            self.send("quit", 0.5)
        finally:
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.terminate()


# --- helpers ----------------------------------------------------------------

def _snapshot(session: _Session, which: str = "devices") -> dict[str, str]:
    """A BlueZ device table as {address: name}. `which` is 'devices' or 'devices Bonded'."""
    devices: dict[str, str] = {}
    for line in session.send(which, wait=2.0):
        m = _DEVICES_ROW.match(line)
        if m:
            devices[m.group(1)] = _real_name(m.group(2), m.group(1))
    return devices


def _real_name(name: str | None, address: str) -> str:
    """BlueZ falls back to the dashed address as alias; that is not a name."""
    name = (name or "").strip()
    return "" if name == address.replace(":", "-") else name


def _info(session: _Session, address: str) -> tuple[dict[str, str], list[str]]:
    """Parsed `info <address>` properties (plus '_type' from the header) and raw lines."""
    lines = session.send(f"info {address}", wait=2.5)
    props: dict[str, str] = {}
    for line in lines:
        header = _INFO_HEADER.search(line)
        if header and header.group(1) == address:
            props["_type"] = header.group(2)
            continue
        if ": " in line and not line.startswith("["):
            key, _, value = line.partition(": ")
            props.setdefault(key.strip(), value.strip())
    return props, lines


_PAIR_DONE = ("Pairing successful", "Failed to pair", "AuthenticationFailed",
              "not available")


def _pair(session: _Session, address: str, budget: float) -> list[str]:
    """Attempt the pairing, retrying once past an `InProgress` collision.

    BlueZ refuses a pairing while a connection attempt to the same device is still
    running; a stalled `connect` therefore used to burn the whole run.
    """
    out = session.send(f"pair {address}", wait=budget, done=_PAIR_DONE)
    if any("inprogress" in line.lower() for line in out):
        time.sleep(3.0)
        out = out + session.send(f"pair {address}", wait=max(15.0, budget * 0.6),
                                 done=_PAIR_DONE)
    return out


def _cleanup(session: _Session, created: set[str]) -> dict[str, str]:
    """Undo the host-side traces of this run.

    Only ever called with addresses this run introduced. Returns {address: outcome}
    where outcome is 'removed', 'absent' (never entered the table — nothing to undo)
    or 'failed' (still there; the report tells the tester to clear it by hand).
    """
    result: dict[str, str] = {}
    for addr in sorted(created):
        session.send(f"disconnect {addr}", 1.5)
        out = session.send(f"remove {addr}", 2.0,
                           done=("has been removed", "not available", "Failed to remove"))
        low = " ".join(out).lower()
        if "has been removed" in low:
            result[addr] = "removed"
        elif "not available" in low or "not found" in low:
            result[addr] = "absent"
        else:
            result[addr] = "failed"
    session.send("scan off", 0.8)
    session.send("agent off", 0.5)
    return result


# --- the check --------------------------------------------------------------

def extract(address: str, addr_kind: str = "", name: str = "", timeout: int = 90,
            connectable: bool | None = None) -> str:
    """Attempt to recover the permanent identity address of `address`.

    The disclosure originates at the **connection**, not at the pairing: the host
    resolves the peer's private address and caches the identity address while handling
    connection completion, below SMP and GATT. The pairing is therefore only a fallback,
    attempted when the connection alone produced nothing attributable — typically a
    target advertising no name, where a new *bond* is the only way to tie an address to
    the peer we just talked to.

    Two preconditions short-circuit *without transmitting anything*: an address kind
    with no private address to unmask, and — when the caller knows it, i.e. when the
    dongle contributed the PDU type — a non-connectable advertiser.
    """
    if addr_kind in _NO_PRIVATE_ADDRESS:
        return _report_not_private(address, name, addr_kind)
    if connectable is False:
        return _report_not_connectable(address, name, addr_kind)

    state: dict = {"address": address, "addr_kind": addr_kind, "name": name,
                   "created": set(), "candidates": [], "cleanup": {}}
    started = time.time()
    session = _Session()
    try:
        session.send("power on", 1.5)
        # Our own discovery: the caller paused its scanner, and BlueZ evicts
        # non-bonded devices from the cache shortly after discovery stops.
        # The window is also a liveness probe: whether the target is still on the air
        # under this address is what tells a rotated address apart from a device that
        # advertises non-connectable, and the two failures look identical otherwise.
        scan_out = session.send("scan le", 8.0)
        hits = [l for l in scan_out if address in l]
        state["seen_live"] = bool(hits)
        rssi = [m.group(1) for l in hits for m in [_RSSI.search(l)] if m]
        state["seen_rssi"] = rssi[-1] if rssi else ""

        baseline = _snapshot(session)
        bonded_before = _snapshot(session, "devices Bonded")
        state["known_before"] = address in baseline
        if not state["known_before"]:
            # Seeded now, not after the detection step, so an error mid-run still
            # leaves the cleanup with something to undo.
            state["created"].add(address)
        target_name = _real_name(name, address) or baseline.get(address, "")

        # 1. Plain LE connection. No GATT write is issued here or anywhere below.
        conn_out = session.send(f"connect {address}", wait=25.0,
                                done=("Connection successful", "Failed to connect",
                                      "not available", "org.bluez.Error"))
        low = [l.lower() for l in conn_out]
        state["connected"] = any("connection successful" in l for l in low)
        state["connect_note"] = next(
            (l for l in conn_out if "failed to connect" in l.lower()
             or "not available" in l.lower() or "org.bluez.error" in l.lower()), "")
        if not state["connected"] and not state["connect_note"]:
            # Neither success nor failure: the attempt is still in flight, and leaving
            # it there makes the pairing below fail with org.bluez.Error.InProgress.
            state["connect_note"] = "no reply within 25s — attempt still in progress"
            state["connect_stalled"] = True
            session.send(f"disconnect {address}", 4.0)

        # 2. Fast Pair presence — advisory only, deliberately does NOT gate the flow.
        props, info_lines = _info(session, address)
        state["fast_pair"] = any("fe2c" in l.lower() for l in info_lines)
        target_name = target_name or _real_name(
            props.get("Name") or props.get("Alias"), address)
        state["name"] = target_name

        # 2b. Evidence D — the disclosure at its actual origin. BlueZ resolves the RPA
        # and caches the identity address while processing the connection completion,
        # at HCI level, before any SMP or GATT interaction. Sampling here means a failed
        # pairing (the common outcome) can no longer hide a leak that already happened,
        # and it is what makes skipping the pairing possible below. Attribution is by
        # name, as everywhere else: our own discovery is still running.
        after_connect = _snapshot(session)
        connect_hits = {a for a in set(after_connect) - set(baseline) - {address}
                        if target_name and after_connect[a] == target_name}
        state["disclosed_on_connect"] = bool(connect_hits)

        # 3. Just Works agent, then the pairing attempt — skipped entirely when the
        # connection already disclosed. Nothing is gained by bonding at that point, and
        # not bonding leaves no trace on the target.
        pair_out: list[str] = []
        if connect_hits:
            state["skipped_pairing"] = True
        else:
            session.send("agent NoInputNoOutput", 1.0)
            agent_out = session.send("default-agent", 1.5)
            state["default_agent"] = any(
                "request successful" in l.lower() for l in agent_out)

            budget = max(15.0, timeout - (time.time() - started))
            pair_out = _pair(session, address, min(40.0, budget))
        low = [l.lower() for l in pair_out]
        state["paired"] = any("pairing successful" in l or "paired: yes" in l for l in low)
        state["bonded"] = any("bonded: yes" in l for l in low)
        state["pair_note"] = next((l for l in pair_out if "failed to pair" in l.lower()), "")

        # 4. Evidence A — another address reported as paired/bonded during the attempt.
        stdout_hits = {m.group(1) for line in pair_out if _PAIR_STATE.search(line)
                       for m in [_ANY_ADDR.search(line)] if m} - {address}

        # Stop our discovery before the after-snapshots, otherwise unrelated neighbours
        # keep entering the table and would look like candidates.
        session.send("scan off", 1.5)

        # 5. Evidence B (strongest) — a *new bond*. Whatever the host just bonded with
        # is by construction the peer we paired with; ambient devices do not bond to us.
        # This is what carries the common case of an RPA target advertising no name.
        bonded_after = _snapshot(session, "devices Bonded")
        bond_hits = set(bonded_after) - set(bonded_before) - {address}

        # 6. Evidence C (weakest) — the plain device-table diff. Only a new entry
        # carrying the *target's* name is attributable to it; everything else is
        # ambient discovery and must not be reported or touched.
        after = _snapshot(session)
        new_addrs = set(after) - set(baseline) - {address}
        diff_hits = {a for a in new_addrs if target_name and after[a] == target_name}
        state["ambient"] = len(new_addrs - diff_hits - bond_hits - stdout_hits
                               - connect_hits)

        # Cleanup only ever covers the target itself (if we introduced it) and the
        # attributed candidates — never a device that was already in the table.
        for cand in sorted(stdout_hits | bond_hits | diff_hits | connect_hits):
            cprops, _ = _info(session, cand)
            evidence = " + ".join(
                (["new bond"] if cand in bond_hits else [])
                + (["connection (RPA resolved)"] if cand in connect_hits else [])
                + (["pairing output"] if cand in stdout_hits else [])
                + (["device-table diff"] if cand in diff_hits
                   and cand not in connect_hits else []))
            state["candidates"].append({
                "address": cand,
                "kind": exposure.addr_kind(cand, cprops.get("_type", "")),
                "name": _real_name(cprops.get("Name") or cprops.get("Alias"), cand)
                        or after.get(cand, ""),
                "evidence": evidence,
                "bonded": cand in bond_hits,
                "on_connect": cand in connect_hits,
            })
            # Remove entries we introduced, plus any we newly *bonded* — the bond is
            # ours to undo even if BlueZ already had the address cached. Anything else
            # pre-existing is left untouched.
            if cand not in baseline or cand in bond_hits:
                state["created"].add(cand)

        # Best candidate: bond evidence wins (whatever we bonded with *is* the peer),
        # then resolution at connection time, then a public address, then a name match.
        state["candidates"].sort(
            key=lambda c: (not c["bonded"], not c["on_connect"], c["kind"] != "pub",
                           bool(target_name) and c["name"] != target_name))
    except (OSError, subprocess.SubprocessError) as err:
        state["error"] = str(err)
    finally:
        try:
            state["cleanup"] = _cleanup(session, state["created"])
        except (OSError, subprocess.SubprocessError):
            pass
        session.close()

    return _report(state)


# --- reporting --------------------------------------------------------------

def _report_not_private(address: str, name: str, kind: str) -> str:
    label, why = _NO_PRIVATE_ADDRESS[kind]
    lines = [f"Identity address disclosure — {address}  {name or '(no name)'}", "",
             f"  {'Advertised address':22} {address} ({label})",
             f"  {'Verdict':22} Nothing to disclose — {why[0]}"]
    lines += [f"  {'':22} {extra}" for extra in why[1:]]
    lines += [f"  {'':22} See the passive exposure check for what this address",
              f"  {'':22} type means for trackability.",
              "", "  No packets were sent."]
    return "\n".join(lines)


def _report_not_connectable(address: str, name: str, kind: str) -> str:
    label, _note = exposure.addr_note(kind)
    return "\n".join([
        f"Identity address disclosure — {address}  {name or '(no name)'}",
        "",
        f"  {'Advertised address':22} {address} ({label})",
        f"  {'Connectable':22} no — broadcast-only advertising (PDU type from the dongle)",
        f"  {'Verdict':22} Nothing to try — a device that does not accept connections",
        f"  {'':22} never opens an LE link, so there is no SMP exchange and no",
        f"  {'':22} identity to distribute. Out of reach of any connection-",
        f"  {'':22} based check, however it is run.",
        "",
        "  No packets were sent.",
    ])


def _verdict(state: dict) -> list[str]:
    best = state["candidates"][0] if state["candidates"] else None
    if best and best["bonded"]:
        # A new bond is the peer we paired with, so its address is the identity the
        # peer distributed — public or random *static*, both are permanent.
        label, _note = exposure.addr_note(best["kind"])
        return [f"IDENTITY ADDRESS DISCLOSED — {best['address']} ({label}).",
                "The host bonded under this address while the target advertised a private",
                "one: the peer distributed its permanent identity. The device's address",
                "privacy does not hold against a host-side pairing attempt."]
    if best and best["on_connect"]:
        label, _note = exposure.addr_note(best["kind"])
        return [f"IDENTITY ADDRESS DISCLOSED — {best['address']} ({label}).",
                "It entered the host device table under the target's name as soon as the",
                "LE connection completed — before any pairing was attempted. The host",
                "resolved the private address at link level, so the device's address",
                "privacy fails against a plain connect: no bond, no SMP exchange and no",
                "GATT interaction were needed to recover the permanent address."]
    if best and best["kind"] == "pub":
        return [f"IDENTITY ADDRESS DISCLOSED — {best['address']} (public).",
                "The private-address privacy of this device does not hold against a",
                "host-side pairing attempt; the permanent BDADDR is recoverable."]
    if best:
        return [f"Possible identity address {best['address']} ({best['kind']}) — a second",
                "address appeared, but it is not a public one. Re-run to confirm it is",
                "stable across the target's RPA rotation before treating it as identity."]
    if state.get("error"):
        return [f"Inconclusive — the check errored out: {state['error']}"]
    if not state.get("connected") and not state.get("paired"):
        if state.get("seen_live"):
            return ["No disclosure observed — the target was still advertising under this",
                    "address yet never answered the connection. That is the signature of",
                    "NON-CONNECTABLE advertising (broadcast only, e.g. Nearby / FindMy",
                    "beacons): no LE connection means no SMP, so no connection-based check",
                    "can reach it. Confirm with the Conn column in the dongle scan."]
        return ["No disclosure observed — the target was not on the air under this address",
                "during the run: the private address had already rotated. Reselect a row",
                "with a low Age and re-run; this says nothing about the device itself."]
    return ["No disclosure observed — no address other than the advertised one entered",
            "the host device table during the attempt."]


def _report(state: dict) -> str:
    address = state["address"]
    label, _note = exposure.addr_note(state.get("addr_kind") or "")

    if state.get("connected"):
        connection = "established"
    elif state.get("connect_stalled"):
        connection = "no reply — attempt cancelled so the pairing could proceed"
    elif state.get("connect_note"):
        connection = f"failed — {state['connect_note']}"
    else:
        connection = "failed"

    if state.get("seen_live"):
        live = "yes — still on the air under this address"
        if state.get("seen_rssi"):
            live += f" (RSSI {state['seen_rssi']})"
    else:
        live = "NO — not seen during the run; the private address had already rotated"

    rows: list[tuple[str, str]] = [
        ("Advertised address", f"{address} ({label})"),
        ("Device name", state.get("name") or "(none)"),
        ("Target advertising", live),
        ("Connection", connection),
        ("Fast Pair service", "present (advisory — not used by this check)"
         if state.get("fast_pair") else "absent (irrelevant — this check does not use it)"),
        ("Pairing attempt",
         "not attempted — the connection already disclosed, so no bond was needed"
         if state.get("skipped_pairing") else
         "paired" if state.get("paired")
         else f"not completed{' — ' + state['pair_note'] if state.get('pair_note') else ''}"),
    ]
    if state.get("bonded"):
        rows.append(("Bond", "target reported Bonded: yes"))
    if state.get("default_agent") is False:
        rows.append(("Agent", "NOT the default agent — another BlueZ client holds it; "
                              "the Just Works flow may not have been used"))

    for i, cand in enumerate(state["candidates"], 1):
        rows.append((f"Candidate {i}",
                     f"{cand['address']} ({cand['kind']}) "
                     f"{cand['name'] or '(no name)'} — via {cand['evidence']}"))
    if not state["candidates"]:
        rows.append(("Candidates", "none — no address attributable to the target entered "
                                   "the device table"))
    if state.get("ambient"):
        rows.append(("Ambient noise", f"{state['ambient']} unrelated device(s) discovered "
                                      "during the run — ignored, not touched"))

    cleanup = state.get("cleanup") or {}
    if cleanup:
        removed = sorted(a for a, outcome in cleanup.items() if outcome == "removed")
        failed = sorted(a for a, outcome in cleanup.items() if outcome == "failed")
        parts = []
        if removed:
            parts.append("removed " + ", ".join(removed))
        if failed:
            parts.append("STILL PRESENT: " + ", ".join(failed) + " (remove by hand)")
        rows.append(("Cleanup (host)",
                     "; ".join(parts) or "nothing to undo — no entry was added"))

    lines = [f"Identity address disclosure — {address}  {state.get('name') or '(no name)'}", ""]
    for key, value in rows:
        lines.append(f"  {key:22} {value}")
    lines.append("")
    verdict = _verdict(state)
    lines.append(f"  {'Verdict':22} {verdict[0]}")
    for extra in verdict[1:]:
        lines.append(f"  {'':22} {extra}")
    lines.append("")
    if state.get("skipped_pairing"):
        footer = _FOOTER_CONNECT_ONLY
    elif state.get("connected") or state.get("paired"):
        footer = _FOOTER_CONNECTED
    else:
        footer = _FOOTER_NOT_CONNECTED
    lines.append(f"  {footer}")
    return "\n".join(lines)
