# 02 — Phase 1: SniffleScanner advertising scan + CLI activity

Status: done (2026-11-04, headless; HW run pending) — `sniff.py` (`SniffleScanner` +
`_parse_adv_data` + decoders), CLI activity `sniff`. Persistence deferred to the engine
(Phase 3/issue 04): standalone scanners don't touch `store`, only `webui/engine` does, so
Phase 1 mirrors them. `SniffleRecord` exposes store-compatible attributes for that merge.
Blocked by: 01 (done)

## Task
Create top-level `sniff.py` with a `SniffleScanner` that runs the Sonoff/Sniffle dongle in
`PASSIVE_SCAN`/`ACTIVE_SCAN` and feeds the shared `livetable` UI, plus a CLI activity. Mirror
`scan_classic.py`'s "independent backend" structure and `scan_live.py`'s threading/record
pattern.

## Implementation
- **Port autodetect**: glob `/dev/serial/by-id/*ITead_Sonoff*` (fallback `/dev/ttyUSB0`),
  return the resolved path; pass it as `SniffleHW(serport=...)`. Mirror `bledev.open_device`
  detection style. Allow `--port` override.
- **`SniffleScanner`** implements the `livetable` interface: `snapshot()`, `is_scanning`,
  `pause()`, `resume()`, `save_csv()`. Daemon thread loops `recv_and_decode()`; on each
  `AdvaMessage`/`ScanRspMessage` update a per-address `SniffleRecord` under a lock. Call
  `mark_and_flush()` once after `setup_sniffer(...)`. `pause()` stops the recv loop cleanly;
  `close()` calls `cmd_reset()`.
- **AD parsing**: Sniffle gives raw `adv_data` bytes (unlike blatann's parsed object). Write a
  small local LTV parser (length-type-value) to pull local name, flags, service UUIDs,
  manufacturer data, service data. Decode protocol/vendor/Fast-Pair with the same intent as
  `scan_live._decode_protocol`/`_fast_pair` (don't import blatann; copy the small tables or
  factor a blatann-free helper).
- **Record/COLUMNS**: address, addr type (from `TxAdd`/RPA/NRPA/Static classification), name,
  RSSI, **Channel**, **PHY**, connectable, manufacturer, type, pkt count, age. Keep discovery
  order.
- **Logging to file** (never the curses screen): mirror `scan_live.run`'s file-handler setup.
- **Persistence**: upsert snapshots into `store` by normalised address so Sniffle sightings
  merge with nRF52/BlueZ. Follow the existing flush pattern.
- **CLI**: add an `ACTIVITIES` entry ("BLE sniff (Sniffle)") routed through
  `_scan_interact_loop`. `python sniff.py --port ...` standalone entry like the other scanners.

## Acceptance (real hardware, dongle is flashed & present)
- Live table fills with the same devices `scan_live` sees, discovery order, Channel/PHY shown.
- `^S` saves a CSV to `csv/`; `^P` pause/resume works; `Q` exits.
- Validated via a pty harness (recreate under `.scratch/`; arrows `\x1bOB`/`\x1bOA`).
- Do NOT commit — report and leave in the working tree.

## Out of scope
Connection following (issue 03); web/MCP (issue 04).
