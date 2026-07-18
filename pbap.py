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
import shlex
import subprocess

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_SESSION = re.compile(r"/org/bluez/obex/\S+session\d+", re.IGNORECASE)

# obexctl needs a moment on startup to acquire the obexd D-Bus client proxy
_STARTUP = 2.0


def _system_python() -> str:
    """The system python3 (has dbus-python); the project venv's does not, and PATH
    may resolve `python3` to the venv when the CLI runs inside it."""
    for cand in ("/usr/bin/python3", "/usr/local/bin/python3"):
        if os.path.exists(cand):
            return cand
    return "python3"


def _run_obexctl(commands: list[tuple[str, float]], timeout: float) -> str:
    """Drive obexctl via a `(sleep; printf ...) | obexctl` pipeline.

    obexctl needs a moment on startup to acquire the obexd D-Bus proxy, and each
    command needs a settle delay; a timed shell pipeline feeds them reliably (a
    stdin-writing thread + communicate() races and loses the input).
    """
    parts = [f"sleep {_STARTUP}"]
    for cmd, wait in commands:
        parts.append("printf '%s\\n' " + shlex.quote(cmd))
        parts.append(f"sleep {wait}")
    parts.append("printf 'quit\\n'")
    script = "(" + "; ".join(parts) + ") | obexctl"
    try:
        res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=timeout)
        out = (res.stdout or "") + (res.stderr or "")
    except subprocess.TimeoutExpired as err:
        out = (err.stdout or "") + (err.stderr or "") if isinstance(err.stdout, str) else ""
    except (OSError, subprocess.SubprocessError) as err:
        return f"obexctl unavailable: {err}"
    return _ANSI.sub("", out).replace("\r", "")


def probe(address: str) -> str:
    """Attempt a PBAP session; report accepted/rejected. No contacts pulled."""
    out = _run_obexctl([(f"connect {address} pbap", 7)], timeout=16)
    low = out.lower()
    if _SESSION.search(out) or "connection successful" in low:
        return ("PBAP access probe: SESSION ACCEPTED — phonebook is reachable on this "
                "(paired) link. Exposure confirmed; no contacts were read.")
    if "not available" in low or "proxy" in low:
        return "PBAP access probe: OBEX client not ready (is obexd/obex.service running?)."
    if "failed" in low or "error" in low or "refused" in low or "reject" in low:
        return ("PBAP access probe: session REJECTED — not paired, PBAP not offered, or "
                "access denied. No phonebook exposure via PBAP.")
    return "PBAP access probe: inconclusive (no session confirmed within the timeout)."


def sample(address: str, count: int = 5) -> str:
    """Pull up to `count` phonebook entries as a bounded proof of exposure.

    Bounded at the protocol level via the BlueZ D-Bus PhonebookAccess1.PullAll
    `MaxCount` filter (obexctl cannot pull a single entry, only the whole book), so
    only `count` entries ever leave the phone. The D-Bus call runs in the system
    python3 (dbus-python is not in the venv) via pbap_pull.py.
    """
    count = max(1, min(count, 20))
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pbap_pull.py")
    env = dict(os.environ)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
    try:
        res = subprocess.run([_system_python(), helper, address, str(count)],
                             capture_output=True, text=True, timeout=45, env=env)
    except subprocess.TimeoutExpired:
        return "PBAP sample: timed out."
    except (OSError, subprocess.SubprocessError) as err:
        return f"PBAP sample: helper failed: {err}"

    data = res.stdout
    if not data.strip():
        detail = res.stderr.strip().splitlines()[-1] if res.stderr.strip() else "no data"
        return (f"PBAP sample: nothing retrieved ({detail}). Is the device paired and "
                f"contacts access granted on the phone?")

    cards = [c for c in data.split("BEGIN:VCARD") if "END:VCARD" in c]
    lines = [f"PBAP sample — {len(cards)} entries (MaxCount={count}, protocol-bounded):", ""]
    for i, card in enumerate(cards):
        lines.append(f"  [entry {i}]")
        for line in ("BEGIN:VCARD" + card).splitlines():
            if line.strip():
                lines.append(f"    {line}")
        lines.append("")
    lines.append("  Bounded at the protocol level (MaxCount) — not a full phonebook dump.")
    return "\n".join(lines)
