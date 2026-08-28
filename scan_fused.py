"""Fused BLE scan: nRF52 dongle **and** host BlueZ, in one table.

The two BLE backends see the same advertising packets at the same instant, so at
any moment they report the *same* address for the same device — including a
rotating private one. That makes them fusable by address, and fusing them fixes a
real workflow defect:

- only the **dongle** reports the advertising PDU type, i.e. whether a device is
  connectable — the single fact that decides whether a connection-based check can
  reach the target at all;
- only **BlueZ** can act on the device (`connect`/`pair`), and only if the target
  is in its own cache.

Triaging on one table and acting from the other meant the private address had
already rotated in between, so the answer never applied to the device you ended up
touching. Here both radios run together: the dongle contributes `Conn` (plus its
richer protocol/Fast Pair decoding), BlueZ contributes the actionable record.

The dongle is optional: without it the table degrades to the BlueZ-only view with
`Conn` unknown, which is exactly `scan_le` — no hard dependency.
"""
from __future__ import annotations

import csv
import os
import time

import livetable
import scan_le
import scan_live
from bledev.fastpair import display as fp_display


def _merge_fast_pair(bluez_fp: str, dongle_fp: str) -> str:
    """Fuse the two backends' Fast Pair fields, preferring a decoded Model ID.

    Only the **dongle** decodes the 3-byte Model ID (BlueZ can report at most a
    bare ``'Yes'``). A plain ``bluez or dongle`` let BlueZ's ``'Yes'`` win and
    masked the Model ID — the very thing a Fast Pair target is named by — so a
    specific Model ID from either side takes precedence over ``'Yes'``.
    """
    for value in (dongle_fp, bluez_fp):
        if value and value != "Yes":
            return value
    return bluez_fp or dongle_fp


def is_viable(rec) -> bool:
    """Whether the identity-disclosure *mechanism* can apply to this record.

    Both conditions are necessary: a *resolvable* private address (the only kind
    with something to unmask — public and static-random are already permanent) and
    connectable advertising (no connection, no address resolution, nothing to leak).

    This says nothing about whether the device is a *legitimate* target: see
    `target_note`.
    """
    return rec.addr_type == "rnd-r" and rec.connectable is True


def is_fast_pair(rec) -> bool:
    """Whether the device advertises Google Fast Pair (service `0xFE2C`).

    Operationally this is the more useful signal of the two. A device only emits
    discoverable Fast Pair advertising **while it is in pairing mode**, which is a
    state someone has to induce deliberately — so a Fast Pair advertiser is both a
    plausible test target and one you can attribute by *name*. An RPA, by design,
    can never be attributed to an owner by looking at it.
    """
    return bool(getattr(rec, "fast_pair", ""))


def target_note(rec) -> str:
    """Short reason this row deserves attention, or '' if it does not.

    Kept as two independent signals rather than one boolean because they answer
    different questions: `FP` says *this is a target you may legitimately act on*,
    `RPA` says *the mechanism would work here*. Only the pair of them together is a
    ready-to-run case; either alone is worth showing and worth explaining.
    """
    fp, viable = is_fast_pair(rec), is_viable(rec)
    if fp and viable:
        return "FP+RPA"
    if fp:
        return "FP"
    if viable:
        return "RPA"
    return ""


def _conn_cell(rec) -> str:
    if rec.connectable is None:
        return "?"
    return "YES" if rec.connectable else "no"


COLUMNS: list[livetable.Column] = [
    ("Address",       19, lambda d: d.address),
    ("Addr",           6, lambda d: d.addr_type or "?"),
    ("Conn",           5, _conn_cell),
    ("Target",         7, lambda d: target_note(d) or "-"),
    ("Name",          18, lambda d: d.name),
    ("RSSI",           5, lambda d: str(d.rssi) if d.rssi else "-"),
    ("Type",          16, lambda d: d.protocol),
    ("FastPair",      16, lambda d: fp_display(d.fast_pair) or "-"),
    ("Manufacturer",  16, lambda d: d.manufacturer),
    ("Bond",           5, lambda d: "Yes" if d.paired else "-"),
    ("Svc",            4, lambda d: str(len(d.services)) if d.services else "-"),
    ("Age",            4, lambda d: f"{int(time.time() - d.last_seen)}s"),
]


class FusedScanner:
    """Both BLE radios at once, merged by address. Implements the livetable interface."""

    def __init__(self, port: str | None = None):
        self.dongle = scan_live.LiveScanner(port)
        self.bluez = scan_le.LeScanner()
        self.dongle_ok = False
        self.dongle_error = ""
        self.only_targets = False

    # --- lifecycle ---------------------------------------------------------
    def open(self) -> None:
        """Open the dongle. Failure is not fatal: the table degrades to BlueZ-only."""
        try:
            self.dongle.open()
            self.dongle_ok = True
        except Exception as err:                     # noqa: BLE001 - report, don't crash
            self.dongle_error = str(err)

    def start(self) -> None:
        if self.dongle_ok:
            self.dongle.start()
        self.bluez.start()

    def close(self) -> None:
        if self.dongle_ok:
            self.dongle.close()
        self.bluez.close()

    # --- livetable interface -----------------------------------------------
    def pause(self) -> None:
        if self.dongle_ok:
            self.dongle.pause()
        self.bluez.pause()

    def resume(self) -> None:
        if self.dongle_ok:
            self.dongle.resume()
        self.bluez.resume()

    @property
    def is_scanning(self) -> bool:
        return self.bluez.is_scanning

    def release_agent(self) -> None:
        """Delegated to the BlueZ side — the dongle has no pairing agent."""
        self.bluez.release_agent()

    def restore_agent(self) -> None:
        self.bluez.restore_agent()

    def snapshot(self) -> list:
        """BlueZ records (the actionable ones) enriched with what only the dongle knows."""
        by_addr = {}
        if self.dongle_ok:
            for src in self.dongle.snapshot():
                # The dongle keys records by blatann's address string ("AA:..,r").
                by_addr[src.address.split(",")[0].upper()] = src

        records = []
        for rec in self.bluez.snapshot():
            src = by_addr.get(rec.address.upper())
            if src is not None:
                rec.connectable = src.connectable
                rec.directed = src.directed
                rec.fp_payload = rec.fp_payload or src.fp_payload
                rec.protocol = rec.protocol or src.protocol
                rec.fast_pair = _merge_fast_pair(rec.fast_pair, src.fast_pair)
                rec.manufacturer = rec.manufacturer or src.manufacturer
                rec.name = rec.name or src.name
            records.append(rec)

        if self.only_targets:
            records = [r for r in records if target_note(r)]
        return records

    def save_csv(self) -> str:
        os.makedirs("csv", exist_ok=True)
        path = f"csv/fused_{int(time.time())}.csv"
        headers = [h for h, _, _ in COLUMNS] + ["FastPairAdv", "Services", "FirstSeen"]
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for d in sorted(self.snapshot(), key=lambda r: r.rssi, reverse=True):
                writer.writerow([fn(d) for _, _, fn in COLUMNS]
                                + ["yes" if is_fast_pair(d) else "",
                                   " ".join(sorted(d.services)),
                                   time.strftime("%Y-%m-%d %H:%M:%S",
                                                 time.localtime(d.first_seen))])
        return path

    # --- optional filter hook picked up by livetable ------------------------
    def toggle_filter(self) -> None:
        self.only_targets = not self.only_targets

    @property
    def filter_hint(self) -> str:
        return "V targets-only:ON" if self.only_targets else "V targets-only"


def run():
    """Entry point: both radios, one table."""
    scanner = FusedScanner()
    scanner.open()
    if not scanner.dongle_ok:
        print(f"  Dongle unavailable ({scanner.dongle_error}) — "
              f"continuing on BlueZ only; the Conn column will read '?'.")
        time.sleep(2)
    scanner.start()
    try:
        selected = livetable.run(scanner, COLUMNS, title="BLE fused")
    finally:
        scanner.close()
    return selected


if __name__ == "__main__":
    run()
