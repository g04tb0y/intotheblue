# Issue 04 — Phase 3: select/act, persistence, web/MCP for Sniffle

Status: ready-for-agent
Depends on: 03 (Phase 2 connection follow — DONE, HW-validated 2026-11-04, commit 59cd35e)

## Why

Phases 1–2 made Sniffle a working third scanner with connection following, but it is
CLI-only, in-memory, and has no "act" beyond viewing. Phase 3 completes the scan→select→act
model for the sniffer: turn an observed connection/device into something you can analyse and
keep, and expose it on the web/MCP front-ends like every other backend. See `spec.md` →
Phasing → Phase 3.

## Increments (ship + HW-validate in order, like Phases 1/2)

### 3a — PCAP export (the headline "act") ← start here
A followed connection is only half-useful on screen: the ATT/GATT content (and anything a
dissector understands) lives in the raw link-layer packets. Export them to a **PCAP** that
Wireshark decodes.
- Buffer the raw decoded packets of a followed connection on its `ConnectionRecord`
  (the `CONNECT_IND` — which carries the AA / CRCInit / channel map a dissector needs — plus
  the followed `DataMessage`s), **bounded** by a cap so memory stays finite.
- `sniff.export_pcap(record, path)` writes them via `sniffle.pcap.PcapBleWriter`
  (`write_packet_message`); output to `pcap/` (new, git-ignored).
- CLI: a per-connection action "Export PCAP" in `_sniff_conn_menu`.
- Acceptance (HW): follow a connection (emulate.py + nRF Connect, as in Phase 2), export,
  open the `.pcap` in Wireshark → the connection + its ATT traffic dissect.

### 3b — connection persistence in `store`
Phase-2 connections are in-memory only. Give them a home so they survive and merge with the
device rows.
- New `connection` table: access address, central + peripheral (normalised addresses),
  address types, interval/latency/timeout, channels, encrypted, terminated, packet counts,
  session_id, first/last seen. Keep it minimal; it is a *new shape* (not a `sighting`).
- `store.upsert_connection(record, session_id)` + a `list_connections(...)` read; both
  blatann-free like the rest of `store`. Link the peripheral to its `device` row by address
  so the Devices view can show "seen connected to <central>".
- `row_to_connection()` to rebuild a record-like object for read-side reuse.

### 3c — engine + web + MCP
Mirror onto the third front-end, reusing the scanner's `CONN_COLUMNS` cell functions.
- `engine`: the single hardware owner gets a **Sniffle** path with its *own* single-owner
  guard (the Sonoff is a separate serial device from the nRF52, so it does not contend with
  the blatann lock, but still one owner at a time). A follow session (scan→select→follow)
  and PCAP export exposed as engine methods; persist connections on the flush thread.
- Web: a connections view / a follow action on the device detail page; PCAP download route.
- MCP: read tool `list_connections` (passive), and the follow/export tools (radio/driving,
  labelled active-ish per the passive/active docstring convention). `_op_log` every
  hardware-touching op in `eventlog`, with `transport="sniffle"`.

## Hard constraints (unchanged, from CLAUDE.md)
Python 3.10, English code/UI, reuse the shared abstractions (no second scan stack), keep
passive/active boundary explicit (following/export are receive-only; label clearly), keep
NOTES.md current in the same change, **do not commit until HW-validated** by the maintainer.

## Grounded API
- `sniffle.pcap.PcapBleWriter(output).write_packet_message(pkt)` + `.close()`; needs each
  `pkt`'s `.ts_epoch`, `.aa`, `.chan`, `.rssi`, `.body`, `.phy`, `.crc_rev`, `.crc_err`
  (all present on the decoded `DPacketMessage`). DLT 256 (BLUETOOTH_LE_LL_WITH_PHDR).
- `store`: tables created in the module's schema string; `upsert_device` pattern for the
  thread-safe single-connection + lock; `row_to_record` for read-side rebuild.
