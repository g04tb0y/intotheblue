"""Classic PBAP (Phonebook Access) probe and bounded sample via BlueZ obexctl.

Two actions, kept distinct:
  - `probe`  : attempt the OBEX PBAP session and report whether it is accepted —
               demonstrates exposure WITHOUT pulling any contact.
  - `sample` : pull a small, capped number of phonebook entries — a proof-of-
               exposure PoC, never a bulk dump.

Constraints, by design:
  - PBAP servers reject sessions from unpaired devices, so this needs a device the
    tester has already PAIRED. This module never pairs, and never bypasses bonding.
  - The sample is capped (default 5 entries) and stored only transiently.

NOTE: obexctl's success/`ls`/vCard-handle output could not be validated against a
real PBAP server in this environment. Parsing is best-effort and defensive; verify
against a real paired phone before relying on it.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import time

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_SESSION = re.compile(r"/org/bluez/obex/\S+session\d+", re.IGNORECASE)
_VCARD_HANDLE = re.compile(r"\b(\d+\.vcf)\b", re.IGNORECASE)

# obexctl needs a moment on startup to acquire the obexd D-Bus client proxy
_STARTUP = 2.0


def _run_obexctl(commands: list[tuple[str, float]], timeout: float) -> str:
    """Drive obexctl with a startup delay, then the (command, wait) script."""
    try:
        proc = subprocess.Popen(["obexctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
    except (OSError, subprocess.SubprocessError) as err:
        return f"obexctl unavailable: {err}"

    def feed():
        time.sleep(_STARTUP)
        for cmd, wait in commands:
            try:
                proc.stdin.write(cmd + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                return
            time.sleep(wait)
        try:
            proc.stdin.write("quit\n")
            proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass

    threading.Thread(target=feed, daemon=True).start()
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    return _ANSI.sub("", out or "").replace("\r", "")


def probe(address: str) -> str:
    """Attempt a PBAP session; report accepted/rejected. No contacts pulled."""
    out = _run_obexctl([(f"connect {address} pbap", 7)], timeout=16)
    low = out.lower()
    if _SESSION.search(out):
        return ("PBAP access probe: SESSION ACCEPTED — phonebook is reachable on this "
                "(paired) link. Exposure confirmed; no contacts were read.")
    if "not available" in low or "proxy" in low:
        return "PBAP access probe: OBEX client not ready (is obexd/obex.service running?)."
    if "failed" in low or "error" in low or "refused" in low or "reject" in low:
        return ("PBAP access probe: session REJECTED — not paired, PBAP not offered, or "
                "access denied. No phonebook exposure via PBAP.")
    return "PBAP access probe: inconclusive (no session confirmed within the timeout)."


def sample(address: str, count: int = 5) -> str:
    """Pull up to `count` phonebook entries as a bounded proof of exposure."""
    count = max(1, min(count, 20))  # hard cap: this is a PoC, not a dump
    tmpdir = tempfile.mkdtemp(prefix="pbap_poc_")
    # First list entries, then pull only the first `count` handles individually.
    commands = [(f"connect {address} pbap", 7), ("cd telecom/pb", 2), ("ls", 3)]
    list_out = _run_obexctl(commands, timeout=18)
    if not _SESSION.search(list_out):
        _cleanup(tmpdir)
        return "PBAP sample: could not open a session (see the access probe first)."

    handles = []
    for m in _VCARD_HANDLE.finditer(list_out):
        h = m.group(1)
        if h not in handles:
            handles.append(h)
        if len(handles) >= count:
            break
    if not handles:
        _cleanup(tmpdir)
        return "PBAP sample: session opened but no vCard entries could be listed."

    pull_cmds = [(f"connect {address} pbap", 7), ("cd telecom/pb", 2)]
    for h in handles:
        pull_cmds.append((f"pull {h} {os.path.join(tmpdir, h)}", 2))
    pull_cmds.append(("disconnect", 1))
    _run_obexctl(pull_cmds, timeout=20 + 3 * len(handles))

    entries = []
    for h in handles:
        path = os.path.join(tmpdir, h)
        if os.path.exists(path):
            try:
                with open(path, "r", errors="replace") as f:
                    entries.append((h, f.read().strip()))
            finally:
                os.remove(path)
    _cleanup(tmpdir)

    if not entries:
        return "PBAP sample: no entries were retrieved."
    lines = [f"PBAP sample — {len(entries)} of first {count} entries (bounded PoC):", ""]
    for h, text in entries:
        lines.append(f"  [{h}]")
        for line in text.splitlines():
            lines.append(f"    {line}")
        lines.append("")
    lines.append("  Bounded proof of exposure — not a full phonebook dump.")
    return "\n".join(lines)


def _cleanup(tmpdir: str) -> None:
    try:
        for name in os.listdir(tmpdir):
            os.remove(os.path.join(tmpdir, name))
        os.rmdir(tmpdir)
    except OSError:
        pass
