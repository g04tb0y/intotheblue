"""Structured JSONL event log for the bench — one durable trace of radio activity.

Why this exists: the engine only ever kept `self._last_error`, a single string
overwritten on every operation, so there was no way to see *what happened* across a
session — which device was connected, what was read/written, when a scan started, why
a GATT browse failed. This module is the single append-only sink for that history,
written as **JSON Lines** (one JSON object per line) so it can be filtered by event,
device or action and exported as-is.

Design choices mirror `store.py`:
  - **stdlib only, no blatann import** — the read side (the webui Logs page, the MCP
    `query_events` tool, headless tests) must load without the dongle driver.
  - **JSONL file, not a DB** — the log is an append-only stream; a flat file under a
    git-ignored dir (`logs/`, like `csv/`/`db/`) matches that shape and needs no schema.
    Path is `logs/intotheblue.jsonl`, override with ``$INTOTHEBLUE_EVENTLOG``
    (tests point it at a temp file).
  - **Passive/active boundary recorded per event** (`boundary` field) — the same
    distinction the UI and the MCP docstrings draw, so the history can show which
    operations transmitted.
  - **Fail-safe** — a logging error must never break a hardware operation, so `emit`
    swallows I/O errors, and `read` skips malformed lines.
"""
from __future__ import annotations

import json
import os
import threading
import time

from store import normalize_address

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs",
                             "intotheblue.jsonl")


class EventLog:
    """Thread-safe append-only JSONL writer + reader for bench events."""

    def __init__(self, path: str | None = None):
        self._path = path or os.environ.get("INTOTHEBLUE_EVENTLOG") or _DEFAULT_PATH
        self._lock = threading.Lock()

    @property
    def path(self) -> str:
        return self._path

    def emit(self, event: str, *, device: str | None = None, transport: str | None = None,
             action: str = "", boundary: str = "active", outcome: str = "ok",
             error: str = "", **detail) -> None:
        """Append one event as a JSON line. Best-effort: never raises.

        `boundary` is "passive" | "active"; `outcome` is "ok" | "error" | "start"
        (a "start" marks the beginning of a long operation so the live tail shows it
        before completion). Extra keyword fields are merged into the record.
        """
        record = {
            "ts": time.time(),
            "event": event,
            "device": normalize_address(device) if device else None,
            "transport": transport,
            "action": action,
            "boundary": boundary,
            "outcome": outcome,
            "error": error,
        }
        if detail:
            record.update(detail)
        line = json.dumps(record, default=str)
        try:
            with self._lock:
                os.makedirs(os.path.dirname(self._path), exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except OSError:
            pass  # logging must never break a hardware operation

    def read(self, *, event: str | None = None, device: str | None = None,
             action: str | None = None, limit: int = 200,
             newest_first: bool = True) -> list[dict]:
        """Return filtered events. `event` matches exactly, `device` by normalised
        address, `action` as a case-insensitive substring. Malformed lines are skipped.
        """
        want_device = normalize_address(device) if device else None
        want_action = action.lower() if action else None
        out: list[dict] = []
        try:
            with self._lock, open(self._path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return []
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event and rec.get("event") != event:
                continue
            if want_device and rec.get("device") != want_device:
                continue
            if want_action and want_action not in (rec.get("action") or "").lower():
                continue
            out.append(rec)
        if newest_first:
            out.reverse()
        if limit and limit > 0:
            out = out[:limit]
        return out

    def tail(self, n: int = 8) -> list[dict]:
        """The most recent `n` events, oldest-first (ready to append to a live tail)."""
        recent = self.read(limit=n, newest_first=True)
        recent.reverse()
        return recent


# Module-level singleton: one event log for the process (shared with the engine).
_eventlog: EventLog | None = None
_eventlog_lock = threading.Lock()


def get_eventlog() -> EventLog:
    global _eventlog
    with _eventlog_lock:
        if _eventlog is None:
            _eventlog = EventLog()
        return _eventlog
