"""Passive bond / pairing-relationship signals from advertising only.

Read-only, sends no packets. Decodes what advertising reveals about *whether* a
device is bonded and *roughly to how many* peers — never *who*: the Fast Pair
account key filter is a one-way Bloom filter and reconnect addresses are RPAs, so
the identity of the bonded account/peer is not recoverable by observation.

Signals:
  - **Fast Pair non-discoverable account key filter** (0xFE2C service data): an
    empty filter means the device holds no account keys → not paired to any
    account; a non-empty filter means it is bonded to at least one account/seeker,
    and the filter grows with the number of stored keys. A show/hide UI-indication
    flag says whether it is soliciting a reconnection notification.
  - **Directed connectable advertising** (ADV_DIRECT_IND): the device is
    reconnecting to a specific bonded peer.
  - **Host-side bond**: BlueZ already knows this device as Paired/Bonded to the
    local adapter.

Non-discoverable Fast Pair frame layout (service data after the 2-byte UUID), per
the Google Fast Pair spec "Provider advertising signal":

    [0]              version + flags (0bVVVVFFFF, currently 0x00)
    [1]              account key filter field header  0bLLLLTTTT
                       L = filter length in bytes, T = type
                       T: 0b0000 show UI · 0b0010 hide UI
    [2 .. 2+L-1]     account key filter (Bloom filter)
    [2+L ..]         optional salt / battery fields (not needed for bond state)

An absent account key data field (payload shorter than 2 bytes, or L == 0) is the
"no account key" case.
"""
from __future__ import annotations

# Account-key-filter field type nibble -> reconnection UI intent
_AKF_UI = {0x0: "show UI", 0x2: "hide UI"}


def fast_pair_bond(payload: bytes | None) -> dict | None:
    """Decode a non-discoverable Fast Pair frame's account key data.

    `payload` is the 0xFE2C service data *after* the 2-byte UUID. Returns None when
    this is not a non-discoverable account-data frame (empty, or a 3-byte
    discoverable Model ID). Otherwise a dict:
        paired (bool), filter_len (int, bytes), ui (str|None), version (int)
    """
    if not payload or len(payload) == 3:
        return None                      # empty or discoverable Model ID
    version = payload[0] >> 4
    if len(payload) < 2:
        return {"paired": False, "filter_len": 0, "ui": None, "version": version}
    header = payload[1]
    filt_len = header >> 4
    ui = _AKF_UI.get(header & 0x0F)
    # Never trust a declared length past the end of the frame.
    if 2 + filt_len > len(payload):
        filt_len = max(0, len(payload) - 2)
    return {"paired": filt_len > 0, "filter_len": filt_len, "ui": ui, "version": version}


def signals(rec) -> list[tuple[str, str, str]]:
    """Passive (label, value, note) bond-relationship rows for a scan record.

    Degrades by backend: the account key filter is only decodable where the raw
    service-data payload is available (the dongle scan); the BlueZ path can only
    confirm Fast Pair is advertised.
    """
    rows: list[tuple[str, str, str]] = []

    payload = getattr(rec, "fp_payload", b"") or b""
    info = fast_pair_bond(payload)
    if info is not None:
        if info["paired"]:
            value = f"bonded — account key filter present ({info['filter_len']} B)"
            note = ("Holds one or more Fast Pair account keys → paired to at least one "
                    "account/seeker. The filter grows with the number of keys but does "
                    "not reveal which account (one-way Bloom filter).")
            if info["ui"]:
                note += f" Reconnection UI indication: {info['ui']}."
        else:
            value = "not paired — empty account key filter"
            note = ("Advertises Fast Pair but stores no account key: not saved to a "
                    "Google account (a plain Bluetooth bond does not write one). Fresh, "
                    "factory reset, or paired without the Fast Pair account-key flow.")
        rows.append(("Fast Pair bond", value, note))
    elif getattr(rec, "fast_pair", "") == "Yes":
        rows.append(("Fast Pair bond", "advertised, filter not decodable here",
                     "BlueZ does not expose the service-data payload — use the dongle "
                     "scan to read the account key filter and the bond state."))

    if getattr(rec, "directed", False):
        rows.append(("Reconnect advertising", "directed (ADV_DIRECT_IND)",
                     "Advertising directly to a specific bonded peer — an active bond "
                     "relationship (the peer address is an RPA; identity not revealed)."))

    if getattr(rec, "paired", False):
        rows.append(("Host bond", "bonded to this adapter",
                     "This device is paired/bonded to the local BlueZ adapter."))

    return rows
