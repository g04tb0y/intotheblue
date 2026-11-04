# Issue 03 — Phase 2: connection following (the unique Sniffle capability)

Status: ready-for-agent
Depends on: 02 (Phase 1 advertising scanner — DONE, HW-validated 2026-11-04)

## Why

Phase 1 proved the Sniffle backend as a third advertising scanner. Phase 2 adds the one
thing a sniffer does that the nRF52/BlueZ controllers cannot: **follow a connection**.
Observing a `CONNECT_IND` surfaces the **phone ↔ device** relationship directly — both MAC
addresses on one link — which is the heart of the standing project goal (identify paired
BT devices). See `spec.md` → Phasing → Phase 2 and `[[goal-identify-paired-bt-devices]]`,
`[[identity-disclosure-target-is-phone-bonded]]`.

## Scope (what ships in this issue)

A **connection-follow mode** of the existing Sniffle backend (not a new one-off script):

- `SniffleScanner(follow=True, targ_mac=..., targ_irk=...)` sets up
  `SnifferMode.CONN_FOLLOW`. No target → catch **every** `CONNECT_IND` on the chosen
  primary channel (attribute after). A target MAC/IRK → `hop3=True`, tracking that peer
  across 37/38/39. Both passive (receive-only — Sniffle never transmits in sniffer mode).
- A new **connection record** keyed by the followed access address: central (`InitA`,
  typically the phone) ↔ peripheral (`AdvA`, the device), both address types, access
  address, connection interval / latency / supervision timeout, hop, used-channel count,
  per-direction packet counts, **encrypted** flag (saw `LL_ENC_REQ`/`LL_START_ENC`),
  **terminated** flag (`LL_TERMINATE_IND`), RSSI, first/last seen.
- **Name enrichment from the device's own advertising**: the scanner keeps the Phase-1
  adv records too, so when a `CONNECT_IND` names an `AdvA` we already saw advertising, its
  advertised local name is copied onto the connection record. (Honest: the name comes from
  the device's advertising, not from decrypted GATT — modern links encrypt before GATT, so
  ATT payloads are unreadable; detecting that is why the `encrypted` flag exists.)
- A second `CONN_COLUMNS` for the live table; `snapshot()`/`save_csv()` branch on mode
  (`csv/sniffle_conn_*.csv`). Reuses `livetable` unchanged.
- CLI — **scan→select→follow** (the usable path): `activity_sniff` is a normal advertising
  scan; selecting a device offers **"Follow this device's next connection"**, which calls
  `SniffleScanner.reconfigure(follow=True, targ_mac=<the address just seen>)` to retarget the
  *same open dongle* to CONN_FOLLOW + hop3, then drives `_scan_interact_loop(CONN_COLUMNS,
  _interact_sniff_conn)`; Q reverts to the advertising scan. Targeting the *current* address
  is deliberate — RPA peers rotate their address, so "see it, chase it" beats a hand-typed
  MAC. **Mandatory operator copy**: following needs a *fresh* `CONNECT_IND`, so the operator
  must trigger the device's (re)connection while following is live.
- `sniff.py --follow [--target AA:BB:..] [--irk HEX]` standalone flag remains for known
  static targets / IRK (RPA) use.

### Why not "follow any" (resolved during HW bring-up, 2026-11-04)
`follow_diag.py` on hardware: 15 s of CONN_FOLLOW with no target captured plenty of
advertising but **zero CONNECT_IND** — parked on one channel, catching a connect request
passively is luck. Reliable capture needs targeting the advertiser (hop3 across 37/38/39),
hence the select→follow flow rather than a "catch everything" mode.

## Out of scope (later phases)

- **ATT/GATT decode from the followed link** (names/services/handles). Unreliable once the
  link is encrypted; proper decode belongs in Wireshark via the Phase-3 **PCAP export**.
  Phase 2 counts the traffic and flags encryption rather than half-parsing ciphertext.
- Persisting connection records to `store` (Phase 2 keeps the relationship on-screen + CSV;
  the connection record is a new shape — decide the store model in Phase 3/issue 04).
- web/MCP surfacing (issue 04).

## Grounded API (vendored `sniffle` v1.11.0)

- `setup_sniffer(mode=CONN_FOLLOW, chan=37, targ_mac=<6 bytes>|None, targ_irk=<bytes>|None,
  hop3=<bool>)`. `hop3` requires a target. targ_mac byte order: reversed of the display
  colon form (`[int(h,16) for h in reversed("AA:BB:..".split(":"))]`, per `sniff_receiver.py`).
- `ConnectIndMessage`: `.InitA`, `.AdvA` (6 bytes LE), `.TxAdd` (InitA type), `.RxAdd`
  (AdvA type), `.aa_conn`, `.Interval` (×1.25 ms), `.Latency`, `.Timeout` (×10 ms), `.ChM`
  (5 bytes), `.Hop`, `.rssi`.
- `DataMessage` family on the followed link: `.aa` (== `aa_conn`), `.data_dir` (1 = P→C,
  0 = C→P). `LlControlMessage.opcode`: 2 = `LL_TERMINATE_IND`, 3 = `LL_ENC_REQ`,
  5 = `LL_START_ENC_REQ`.
- Addresses via `str_mac` (upper-case colon, matches store normalisation).

## Acceptance (hardware)

Start the sniffer in follow mode, trigger a reconnection of a known device (toggle its BT):
a **connection record appears naming both peers** (phone InitA ↔ device AdvA) with access
address and connection interval; the `encrypted` flag flips once the phone starts bonding;
`Ctrl+S` writes `csv/sniffle_conn_*.csv`; `Ctrl+P`/`Q` behave as in Phase 1. Validate via a
pty harness under `.scratch/` (not committed). **Do not commit until the maintainer
validates on hardware.**
