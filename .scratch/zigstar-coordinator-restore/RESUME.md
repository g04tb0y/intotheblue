# ZigStar CC2652P — restore Zigbee coordinator firmware (RESUME)

Status: **BLOCKED on host→chip TX (adapter TXD → module DIO12).** Paused 2026-10-31.
Pick this up when you have the patience — everything (tool, deps, firmware) is already staged here.

## Goal
Put a working **Zigbee coordinator** back on the ZigStar LAN GW's CC2652P. It currently
runs **Sniffle** (the Z-Stack coordinator was erased during the 2026-09-15 Approach B
Sniffle flash). Only the CC2652P radio needs reflashing — the **ESP32 (WT32-ETH01)
gateway firmware is intact and must NOT be touched**.

Chosen path: direct external UART → `cc2538-bsl` over BSL. (Network/vendor path via the
ESP32 bridge was the other option; we went serial because the adapter is already wired.)

## What's staged in this folder
- `cc2538-bsl/` — JelmerT/cc2538-bsl (script at `cc2538-bsl/cc2538_bsl/cc2538_bsl.py`)
- `.venv/` — venv with `pyserial` + `intelhex`
- `fw/CC1352P2_CC2652P_launchpad_coordinator_20250321.hex` — the coordinator to flash
  - sha256 `c2d3a05fe7146b6d57424571629f6efd9166395202babb86639849e9af31a1ff`
  - **launchpad** variant (module is **RF-BM-2652P2**), NOT `_other`.
  - source: Koenkk Z-Stack-firmware release `Z-Stack_3.x.0_coordinator_20250321`.

## The flash command (run once the TX line is fixed and chip is in BSL)
```bash
cd /opt/repo/intotheblue/.scratch/zigstar-coordinator-restore
./.venv/bin/python cc2538-bsl/cc2538_bsl/cc2538_bsl.py -p /dev/ttyUSB0 -b 500000 \
  -e -w -v fw/CC1352P2_CC2652P_launchpad_coordinator_20250321.hex
```
- Manual BSL (no DTR/RTS wired): **omit** any `--bootloader-*` flag (shown above) so the
  tool doesn't pulse the lines and kick the chip out of BSL.
- Auto BSL (if you wire DTR→RESET, RTS→BSL, e.g. the DSD SH-U09C5): add
  `--bootloader-sonoff-usb`.
- The connect phase prints the chip ID *before* erasing, so a failed sync does NO harm
  (it never reaches erase).

## Wiring
Adapter (DSD SH-U09C5 FT232 **or** classic CP2102 — both tried, both fine):
`VCC · GND · TXD · RXD · RTS · CTS`. **Voltage jumper = 3V3** (5V kills the CC2652P).

| Adapter | → CC2652P module | notes |
|---------|------------------|-------|
| TXD | **DIO12** (chip RX) | ← **the broken link, see below** |
| RXD | **DIO13** (chip TX) | proven working |
| GND | GND | proven working |
| VCC (3V3) | 3V3 | power the module standalone |

(DIO12=RX / DIO13=TX confirmed in NOTES.md, the pins used by both BSL and Sniffle.)

## Diagnosis so far (what's proven)
- ✓ Adapter TX alive — TXD↔RXD **loopback OK** at 115200 and 500000.
- ✓ Adapter RX + chip TX (DIO13) — received ~1500 bytes of app chatter off the chip.
- ✓ Chip alive and **enters BSL** — holding BSL before VCC makes the **red LED stop
  flickering** (= app does not run = in ROM bootloader). Flickering LED = Sniffle running
  = not in BSL.
- ✗ **host→chip TX (TXD → DIO12)** — `cc2538-bsl` gets `Timeout waiting for ACK/NACK
  after 'Synch (0x55 0x55)'` at every baud (115200/230400/500000), even while in BSL.
  The ROM bootloader is not receiving our bytes.

This is the same host→chip TX block first seen on 2026-10-15. Approach B (2026-09-15)
got past it on DIO12/DIO13 with a CP2102 + manual BSL **while Z-Stack was running**;
it no longer syncs now.

## Prime suspects for the TX block (check in this order)
1. **ESP32 contention on DIO12.** If the ZigStar's own USB/power is connected, the ESP32
   drives the CC2652P RX net and overrides the adapter's TXD. → **Unplug the board's own
   power; power the module ONLY from the adapter 3V3.** Cheapest fix, do this first.
2. **TXD not actually on DIO12.** Continuity-test adapter TXD ↔ module DIO12 pad; make
   sure it didn't land on DIO13 (where RXD already is) or a wrong pad. **Need the ZigStar
   board photo/schematic to point at the exact DIO12 pad — not yet provided.**
3. **Series element / level** on the RX pad (NOTES 2026-10-15: no pull-ups on TX_Z/RX_Z).

## Guaranteed fallback if the serial path stays dead
**JTAG/SWD** mass-erase + flash with an **XDS110** or **SEGGER J-Link**. A debug-port
erase ignores the CCFG backdoor/UART config entirely and recovers any CC26x2. The ZigStar
breaks out the pins (TMS/TCK/GND/3V3); erase then flash the same coordinator hex (or via
UniFlash/OpenOCD).

## After a successful flash — verify
- Re-run the command: expect `CC1350/CC2652 … Primary IEEE 00:12:4B:…`, then
  `Erase… Write… Verify… OK`.
- Point Zigbee2MQTT/ZHA at the gateway (`tcp://<IP>:6638`) or check the gateway web UI
  shows a Zigbee firmware version again.

## TODO when resuming
- [ ] Unplug ZigStar board power (kill ESP32), power module from adapter only.
- [ ] Continuity: adapter TXD ↔ module DIO12.
- [ ] Provide the ZigStar board photo so the DIO12 pad can be confirmed.
- [ ] Enter BSL (LED steady), run the flash command above.
- [ ] If still no sync → JTAG/SWD recovery.
