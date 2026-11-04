# Spec — SniffleScanner: CC2652P (Sonoff) as a third scan backend

Status: ready-for-agent

## Context (read first)

The hardware is **ready**: a Sonoff ZBDongle-P (CC2652P) was flashed with **Sniffle
v1.11.0, full/CP2102N 2 Mbps** variant on 2026-11-04 and **runtime-verified** — it decodes
clean BLE advertising at 2 Mbps, zero errors (see `NOTES.md`, "FLASHED & CRC-VERIFIED").

- Dongle port (by-id, stable): `/dev/serial/by-id/usb-ITead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_*-if00-port0` → `/dev/ttyUSB0`.
- Sniffle repo is cloned at `.scratch/sonoff-sniffle-flash/Sniffle/` — the Python package is
  `python_cli/sniffle/` (authoritative API source; read it, don't trust memory).
- Flash helper + firmware live under `.scratch/sonoff-sniffle-flash/`.

This is the design sketched in `NOTES.md` → "Sniffle integration design (sketch, 2026-08-28)".
Re-read that section; this spec makes it concrete against the now-present hardware and the
real `sniffle` v1.11.0 API.

## Goal

Add the CC2652P/Sniffle dongle as a **third, independent scan backend**, slotting into the
existing **scan → select → act** model next to BLE (nRF52/blatann) and Classic (BlueZ). The
unique capability Sniffle unlocks is **connection following**: observing a `CONNECT_IND` and
then the followed data-channel traffic, which surfaces a **phone ↔ device** relationship
directly — the heart of the standing project goal (identify paired BT devices). See
`[[goal-identify-paired-bt-devices]]`.

## Hard constraints (from CLAUDE.md)

- **Python 3.10** (`.venv310`). pyserial is already present in that venv; Sniffle needs only
  pyserial (no blatann — this backend is fully independent, like `scan_classic`).
- Code and all UI text in **English**. Module docstrings must state the **why**.
- **No standalone one-off scripts** — implement as the `livetable` scanner interface + an
  `ACTIVITIES` entry (CLI) and `engine`/`store` methods + a route/tool (web/MCP). Reuse, don't
  duplicate.
- Keep passive/active boundary explicit: passive scan vs connection-follow are both receive-
  only (Sniffle never transmits in sniffer mode), but label clearly in the UI; any future
  `cmd_transmit`/`initiate_conn` use is active and must be gated.
- **Do not commit** until the maintainer validates on real hardware. Keep NOTES.md current in
  the same change.

## Real API (grounded in the cloned `sniffle` v1.11.0 package)

`sniffle/sniffle_hw.py`:
- `SniffleHW(serport=None, logger=None, timeout=None, baudrate=None)` — `serport=None`
  autodetects via `serial.tools.list_ports.comports`. We should pass our **by-id** path
  (autodetect it the way `bledev.open_device` does for the nRF52) for determinism.
- `setup_sniffer(mode=SnifferMode.CONN_FOLLOW, chan=37, targ_mac=None, targ_irk=None,
  hop3=False, ext_adv=False, coded_phy=False, rssi_min=-128, interval_preload=[],
  phy_preload=PhyMode.PHY_2M, pause_done=False, validate_crc=True, txPower=5)`.
  - `hop3=True` requires a target (`targ_mac` or `targ_irk`).
  - `ext_adv=True` needed for BT5 extended advertising / coded PHY.
- `mark_and_flush()` — call once after setup to drop stale buffered packets.
- `recv_and_decode()` — blocking; returns a decoded message object. Loop this in a thread.
- `cmd_reset()` — reset the sniffer state (call on close).

`sniffle/constants.py`: `SnifferMode.{CONN_FOLLOW=0, PASSIVE_SCAN=1, ACTIVE_SCAN=2}`,
`PhyMode.{PHY_1M, PHY_2M, PHY_CODED,...}`.

`sniffle/packet_decoder.py` message classes and fields:
- base `PacketMessage`: `.rssi`, `.chan`, `.phy`, `.aa`, `.ts` (timestamp), `.body`.
- `AdvaMessage` (→ `AdvIndMessage`, `AdvNonconnIndMessage`, `ScanRspMessage`,
  `AdvScanIndMessage`): `.AdvA` (6 bytes), `.adv_data`, `.TxAdd` (addr type bit).
- `AdvDirectIndMessage`: directed (reconnect to a bonded peer).
- `ConnectIndMessage` (**the relationship**): `.InitA` (initiator = phone), `.AdvA`
  (advertiser = device), `.aa_conn`, `.Interval`, `.Latency`, `.Timeout`, `.ChM`, `.Hop`.
- `DataMessage` / `LlDataMessage` / `LlControlMessage`: the **followed connection's**
  data-channel traffic (ATT/GATT reveals name/services while the device is connected).
- `sniffle/pcap.py`: PCAP writer for export (select/act).

MAC byte order: Sniffle addresses are little-endian 6-byte; use the package's own
`str_mac`/`str_mac2` helpers for display and normalise to the store's upper-case, colon form
(drop any suffix) to match `store` address normalisation.

## Design

### Packaging decision (resolve in issue 01)
`sniffle` is not on our requirements. Two options:
- **(A, recommended) Vendor** `python_cli/sniffle/` into the repo as top-level `sniffle/`
  (like `bledev/` and `webui/` are in-repo packages, not pip-installed). Pin the Sniffle
  version in a short `sniffle/VENDORED.md` (source commit/tag v1.11.0) so upgrades are
  traceable. Add `pyserial` to `requirements.txt` (currently implicit via the venv).
- (B) Add Sniffle as a git/PyPI dependency. Rejected unless packaging proves clean — the
  repo's convention is in-repo packages and offline-friendly vendoring.

### `sniff.py` (new top-level module) — mirrors `scan_classic.py`
Independent backend wrapping `SniffleHW`. Contains:
- Port autodetect for the Sonoff by-id glob (fallback `/dev/ttyUSB0`), mirroring
  `bledev.open_device`'s detection but for the ITead/CP210x string.
- `SniffleScanner` implementing the `livetable` informal interface:
  `snapshot() -> list[record]`, `is_scanning` (property), `pause()`, `resume()`,
  `save_csv() -> str`. A daemon thread loops `recv_and_decode()` and updates records under a
  lock, exactly like `LiveScanner._run`. Route Sniffle/pyserial logging to a **file** (never
  the curses screen — see `scan_live.run`).
- A `COLUMNS` list of `(header, width, fn)` tuples.

### Phasing (ship in order; each is independently validatable on hardware)

**Phase 1 — advertising scan (lowest risk, proves the backend).**
`SnifferMode.PASSIVE_SCAN` (or `ACTIVE_SCAN` to elicit scan responses). Record model mirrors
`scan_live.DeviceRecord` keyed by address: address, addr type, name (from `adv_data`), RSSI,
channel, PHY, connectable, packet count, age. Reuse as much decoding as sensible — but note
the inputs differ (raw Sniffle `adv_data` bytes vs blatann's parsed `AdvertisingData`), so a
small AD-structure parser is needed (or reuse one if a shared parser is introduced). Persist
each snapshot to `store` on a flush thread (CLI loop / engine already do this pattern). This
phase makes the dongle a working third scanner in the existing UI with **no new record model
on screen** beyond a couple of Sniffle-specific columns (Channel/PHY).

**Phase 2 — connection following (the unique capability).**
`SnifferMode.CONN_FOLLOW`, `hop3=True` with a target when known. New **connection** record
keyed by the observed link: show both peers (**InitA=phone ↔ AdvA=device**), access address,
conn interval, and enrich the device side from the followed link (name/services parsed from
ATT/GATT in `DataMessage`s). This is the first surface that shows a phone↔device pairing
directly. **Operational UI copy is mandatory:** following needs the `CONNECT_IND`, so the UI
must instruct the operator to **start the sniffer, then trigger a reconnection** (device
off/on or toggle BT). For an **RPA** device, filter by **IRK** if known; otherwise no filter,
catch every `CONNECT_IND`, attribute after. See `[[identity-disclosure-target-is-phone-bonded]]`.

**Phase 3 — select/act + web/MCP.**
Per-device/per-connection actions: **PCAP export** (`sniffle.pcap`), dump captured GATT,
correlate the two peers. Then mirror onto `engine` + a web route + an MCP tool, reusing the
scanner's `COLUMNS` cell functions (web table matches CLI exactly, per the architecture). The
engine is the single hardware owner and serialises ops behind one lock — the Sniffle dongle
is a *separate* serial device from the nRF52, so it does not contend with the blatann lock,
but still give it its own single-owner guard.

### CLI integration
Add one `ACTIVITIES` entry (e.g. "BLE sniff (Sniffle)") and drive it through
`_scan_interact_loop` like the other scanners. Per-device actions via the data-driven
`_device_action(...)` helper. No second copy of scan logic.

### Store integration
Reuse `store.py` unchanged where possible: the Phase-1 advertising record should upsert into
the same `device` table by normalised address (latest-non-empty-wins) so Sniffle sightings
merge with nRF52/BlueZ sightings of the same device. Phase-2 **connection** records are a new
shape — decide whether they extend `sighting`/a new table or are export-only for now (keep
Phase 2's persistence minimal; the relationship can first be a CSV/PCAP + on-screen record).

## Testing / validation
- No automated suite. Validate by running against the **real flashed dongle** (present now).
- Curses UI: drive through a **pty** harness (not committed; recreate under `.scratch/`),
  sending keys and asserting on output / generated CSV. Application-cursor arrows are
  `\x1bOB`/`\x1bOA`.
- Headless unit-test the AD/connection decoders where they have no hardware dependency.
- Phase 1 acceptance: the live table fills with the same devices `scan_live` sees, in
  discovery order, Channel/PHY columns populated, CSV saves, pause/resume works.
- Phase 2 acceptance: starting the sniffer and triggering a reconnection of a known device
  produces a connection record naming both peers.
- **Do not commit**; report and leave in the working tree for the maintainer to validate.

## Open questions
- Vendor vs dependency for the `sniffle` package (issue 01).
- Shared AD-structure parser: is one worth extracting so `scan_live` (blatann-parsed) and
  `sniff` (raw bytes) share protocol/Fast-Pair decoding, or keep a small local parser in
  `sniff.py` for now? Lean: local parser first, extract later if duplication bites.
- Connection-record persistence model (Phase 2) — extend `store` or export-only first.

## Tickets
See `issues/`. Implement in order: 01 (packaging) → 02 (Phase 1 scanner+CLI) →
03 (Phase 2 conn-follow) → 04 (Phase 3 select/act + web/MCP).
