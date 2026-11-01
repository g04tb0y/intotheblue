# NOTES

Working memory for this project. See `CLAUDE.md` for how it's used. Git holds the
detailed change history; this file holds rationale, plans, and open questions.

## Resume here — 2026-11-01, web UI + MCP server + SQLite persistence (built, headless-validated; NOT yet hardware-validated)

*Added a third front-end over the existing scanners: a FastAPI process serving a web
UI (Jinja2 + HTMX), a JSON API and an MCP server (FastMCP, streamable HTTP at `/mcp`),
all backed by a new stdlib-sqlite `store.py`. New code: `store.py`, `webui/`
(`engine.py`, `app.py`, `api.py`, `mcp_server.py`, templates, static + vendored htmx).
It is an **in-repo package, not installed** (like `bledev/`) — no `python -m`, no install.
Single entry point `intotheblue.py --cli | --webui` (new). Modified: `cli.py` (new `webui`
activity + direct `python cli.py <activity>` launch), `requirements.txt`
(fastapi/uvicorn/jinja2/mcp<2), `.gitignore` (`db/`), `CLAUDE.md`.*

**What was asked** (maintainer, in plan): a web interface (at least a scans page and a
page to navigate all device info + GATT), an MCP server, and a lightweight DB to persist
scans/info. Plan decisions: the **web server owns the hardware** (CLI⇄web mutually
exclusive), **Jinja2 + HTMX** (no build step), **MCP mounted HTTP** in the same process
(shares the engine/DB, no dongle contention).

**Design.** `webui/engine.py` is the single hardware owner — one active scanner, one
lock serialising every dongle op, a background flush thread persisting `snapshot()` to
`store`, and `browse_gatt()` that **pauses the scan and connects on the same open
`BleDevice`** (the `_scan_interact_loop` invariant), reusing `client.*`/`capabilities`/
`exposure`. The web live table reuses each scanner's `COLUMNS` cell fns → identical to
the CLI. `store.py` imports no blatann (read side + tests load without hardware); upsert
is latest-non-empty-wins with the `_merge_fast_pair` precedence (decoded Model ID beats
`"Yes"`); `row_to_record()` lets the passive analysers run on DB rows unchanged.

**Validated headless (no dongle):** store round-trip + merge precedence; uvicorn served
`/` (scans), `/devices` (list + filters), `/devices/{addr}` (info + exposure + GATT),
the HTMX partials, and the JSON API against a seeded DB; a real MCP client over
streamable HTTP listed 9 tools and `list_devices`/`exposure_report`/`scan_status`
returned correct data. **Gotchas resolved:** Starlette 1.7 `TemplateResponse(request,
name, context)` signature (name-first crashes with "unhashable dict"); FastMCP lifespan
must be driven by the host (`session_manager.run()` in the FastAPI lifespan); FastAPI
0.162 ships OpenTelemetry instrumentation (inert without an OTLP endpoint, but disabled
explicitly in `webui/__init__` for the offline posture).

**NOT yet validated on hardware** — the real checkpoint (maintainer): run a BLE/fused/
Classic scan from the web, watch the live table, open a device, **Browse GATT (connect)**,
confirm GATT + exposure render and **persist** across a restart. Do not commit until that
passes (Rules of conduct). Also to decide on hardware: whether the 1 s HTMX table poll +
the 2 s flush interval are comfortable during a live scan.

## Resume here — 2026-10-31, restoring the ZigStar Zigbee coordinator (PAUSED, host→chip TX still blocked)

*Trying to put a Z-Stack coordinator back on the ZigStar CC2652P (Sniffle erased it).
Full staged tooling + firmware + diagnosis + next steps in
`.scratch/zigstar-coordinator-restore/RESUME.md`. Blocked on the same host→chip TX issue
(adapter TXD → DIO12): loopback proves the adapter TX is fine and the chip enters BSL
(red LED stops flickering), but `cc2538-bsl` never syncs — prime suspects: ESP32
contention on DIO12 (power the module from the adapter only) or the TXD wire/pad not on
DIO12. Fallback: JTAG/SWD. Firmware = Koenkk `CC1352P2_CC2652P_launchpad_coordinator_20250321.hex`
(launchpad, for RF-BM-2652P2).*

## Resume here — 2026-10-30, Sonoff ZBDongle-P as the clean Sniffle host (bypasses the ZigStar TX block)

*The ZigStar bring-up is stuck on the host→CC2652P TX line (see the 2026-10-15 section
below) — a hardware wiring problem on an intact board we refuse to modify. Decision: get
Sniffle running on a **Sonoff ZBDongle-P** instead, which needs no soldering and no
external FTDI. Same radio (CC2652P, Sniffle-supported); its onboard CP210x bridge is
wired straight to the CC2652P UART and BSL entry is automatic over DTR/RTS.*

**Why it sidesteps everything that blocked the ZigStar**
- Radio reachable directly over USB (on the ZigStar the CH340 reached the ESP32, not the
  radio, forcing the external-UART Approach B — which is where TX died).
- Automatic BSL: `cc2538-bsl --bootloader-sonoff-usb` enters the bootloader by toggling
  DTR/RTS; no hold-BSL + power-cycle, no manual wiring.
- 2 Mbps bridge: recent units carry a **CP2102N** (handles Sniffle's fixed 2 Mbps). Some
  2022 units carry a plain **CP2102** (~1 Mbaud cap) → those need the reduced-rate build.

**The one real decision: firmware variant (follows the bridge chip)**
- **CP2102N** → `sniffle_cc1352p1_cc2652p1.hex` (full 2 Mbps).
- **plain CP2102** → `sniffle_cc1352p1_cc2652p1_1M.hex` (921600).
- Check first: `lsusb -v -d 10c4: | grep -i iProduct` (or the `/dev/serial/by-id/` name).
  Flashing the wrong variant → garbled decode or, worst case, a locked bootloader
  (recovery via JTAG/SWD). This is the brick risk.

**Flashing procedure**
1. Tools (once): `git clone https://github.com/JelmerT/cc2538-bsl` and
   `pip install pyserial intelhex`. Firmware: Sniffle releases page
   (`github.com/nccgroup/Sniffle` → Releases), grab the `.hex` for the variant above.
2. Plug the dongle in; find the port via `ls -l /dev/serial/by-id/` (prefer the stable
   by-id path over `/dev/ttyUSB0`).
3. Flash (erase/write/verify, auto-BSL):
   ```
   python3 cc2538-bsl.py -p <PORT> --bootloader-sonoff-usb -ewv sniffle_cc1352p1_cc2652p1.hex
   ```
4. Known post-flash bug (cc2538-bsl, as of 2025-01-10): it may fail to **reset** the
   CC2652P after a good write. If erase/write/verify lines look fine, the flash is fine —
   just **unplug/replug**. Kismet also notes removing `python3-magic` can help.
5. Verify Sniffle answers: `python3 python_cli/sniff_receiver.py -s <PORT>` → should print
   BLE packets. Confirmed working on the ZBDongle-P per Kismet's Sniffle datasource docs.

**Helper:** `.scratch/sonoff-sniffle-flash/flash_sonoff_sniffle.sh` wraps steps 1b–4:
autodetects port + bridge chip, picks the matching `.hex`, prints the plan, confirms, runs
cc2538-bsl, and flags the reset bug. `--dry-run` shows the detected plan without flashing;
`--variant full|1m` forces the choice if autodetect can't tell. See the folder's README.

**Status: NOT YET RUN on real hardware** — dongle to be flashed. Once it sniffs cleanly,
the next step is unchanged from the ZigStar plan: wrap `SniffleHW` as a `livetable`
scanner (`SniffleScanner`), slotting into the existing scan→select→act model.

**Sources:** NCC Group Sniffle README (`github.com/nccgroup/Sniffle`); Kismet "Sniffle BLE
(Sonoff ZBDongle-P)" datasource docs (kismetwireless.net).

## Resume here — 2026-10-15, FTDI adapter wired to CC2652P: RX works, TX blocked

*FTDI adapter (DSD TECH SH-U09C5, FT232RNL, 3 Mbps max) arrived and was wired to the
CC2652P module (RF-BM-2652P2) on the ZigStar LAN Gateway board. Sniffle firmware is
already flashed (from the earlier CP2102 session). RX direction is proven; TX direction
does not work — the FTDI's output signal does not reach DIO12.*

**What works:**
- FTDI detected as `/dev/ttyUSB0`, loopback verified at 115200 / 921600 / 2000000 baud
- RX (CC2652P → host): clean base64-decoded BLE advertising at 2 Mbps, zero decode errors
- FTDI voltage confirmed 3.3V (VCC selector on the adapter)

**What does NOT work:**
- TX (host → CC2652P): all commands ignored — 0/10 marker echoes, reset (0x17) has no
  effect, silence (impossible AA) has no effect, 100 rapid markers → 0 responses, TX
  flood had no effect on RX stream rate
- Break test (pyserial `break_condition=True`, holds TXD at 0V): the pad on the CC2652P
  where TXD is wired reads **3.3V consistently**, with rare brief drops to 2.4V — the
  FTDI output is not reaching DIO12

**Eliminated causes:**
- ESP32 contention: removed the WT32-ETH01 module entirely — break test result unchanged
- CH340C contention: ZigStar USB disconnected — no change
- Pin assignment: confirmed DIO12 (RX) / DIO13 (TX) for both the BSL and the Sniffle
  firmware (CC1352P1_LAUNCHXL XDS110UART component)
- FTDI hardware: loopback at 2M works; the adapter transmits fine
- Schematic pull-ups: no pull-up resistors on TX_Z/RX_Z in the ZigStar schematic (R2/R3
  are LED current-limiting resistors)
- DTR/RTS: tested all 4 combinations, no effect

**Causa non identificata.** Ipotesi non verificate:
- Contatto fisico scarso del filo TXD sul pad DIO12
- Filo TXD sul pad sbagliato (DIO12 e DIO13 sono pad adiacenti sul modulo)
- Problema del driver pyserial/FT232R con `break_condition` su Linux (il break test
  potrebbe non essere affidabile come diagnostica)
- Problema di integrità del segnale a 2 Mbps specifico del percorso TX (non testato a
  baud inferiori perché Sniffle è fisso a 2M)

**To resume:**
1. Il problema è aperto — il TX non funziona e la causa è ignota.
2. Se/quando TX funziona → build the `SniffleScanner` (livetable interface).
3. Vincolo: NON modificare/compromettere la scheda ZigStar (no dissaldature, no taglio
   piste). Soluzioni con la scheda intatta (meno il WT32-ETH01 rimovibile).

## Resume here — 2026-08-28, disclosure validated against a real Fast Pair target: NEGATIVE

*Executed the full test plan against a real **Redmi Buds 6 Play** (see the standalone
`docs/test-plan-redmi-buds-6.md` for the run and evidence). The one claim the whole
2026-07-31 rework rested on did **not** reproduce.*

**Result.** On the Redmi Buds 6 Play the identity-address disclosure does not happen:
the host opens an unauthenticated LE connection and reads full GATT (name
"Redmi Buds 6 Play", FE2C KBP chars `fe2c1233`…`1237`), but a plain Just Works bond
is **refused** (`org.bluez.Error.AuthenticationCanceled`) and **no permanent identity
address ever enters the BlueZ device table** — tested virgin (pairing mode) and
phone-bonded (reconnect RPA). `intotheblue`'s `identity.extract` and the reference
tool **Whisper_Bully** (`wb.py`, with `sudo` + `bleak`) fail **identically**
(`Failed to extract`), so this is a device/mechanism property, **not an intotheblue
bug** — `identity.extract` reported the truthful "No disclosure observed".

**Why (and a correction to the assumption below).** Resolving an RPA needs the peer's
IRK, which the host gets only when an SMP bond **completes**. This host never bonded
the buds → no IRK → it cannot resolve the RPA on connection, exactly as BLE privacy
prescribes. So the 2026-07-31 framing — "the disclosure happens at connection
completion, before any SMP; pairing is a fallback" — is a reading of Whisper_Bully's
README that **does not hold empirically** on this device (WB's README claims Stage-1
resolution at `LL_CONNECTION_COMPLETE` even on patched devices; it did not reproduce,
and WB itself failed to extract). The device being bonded to a *phone* does not help:
the phone holds the IRK, not our host.

**Consequence for the checkpoint.** `72b680b` is **not** promoted to a validated
"disclosure works" checkpoint. The recon/scan half remains proven; the disclosure it
exists to demonstrate does not reproduce on a current Fast Pair accessory.

**Caveat — RESOLVED with an in-house positive control.** The open question was whether
the negative meant "device not vulnerable" or "this BlueZ version doesn't exhibit the
leak at all". Built a positive control instead of buying one: `emulate.py
--positive-control` makes the dongle advertise a **Resolvable Private Address** and
accept an unauthenticated Just Works bond (blatann `set_privacy_settings` +
`set_security_params(bond=True, io_caps=NONE)`; the peripheral always distributes its
`id_key`). Ran `identity.extract` (host/hci0) against that RPA and it worked end to end:
`Bonded: yes` → **`IDENTITY ADDRESS DISCLOSED — CE:C1:1B:51:DD:23`** (the dongle's static
identity), correctly attributed by new-bond + name. So the pipeline is **proven correct
in this environment**, and the Redmi negative is a genuine **device property** (it
refuses the bond), not a tooling/stack defect. Optional remaining nicety: confirm on a
real-world Fast Pair accessory that does *not* enforce the bond gate.

## Resume here — 2026-07-31, recon stage realigned and committed; disclosure untested

*Short version: the scan side is done and pushed (`72b680b` on `origin/devel`). What is
left needs a Fast Pair accessory in pairing mode — see **Test plan** below, which is
written to be run cold.*

**Committed and pushed** as `72b680b` on `origin/devel`, at the maintainer's explicit
request and ahead of the usual rule — new: `identity.py`, `scan_le.py`, `scan_fused.py`;
modified: `cli.py`, `exposure.py`, `scan_classic.py`, `livetable.py`, `README.md`, this
file. It is a save-point, **not a validated checkpoint**: the scan half is proven live,
the disclosure the check exists for has never run against a real target ("poi la
testeremo"). Do not list it under *Checkpoint* until it has.

**The USB fault is cleared.** After rebooting host and guest, the dongle opens normally
(`open_device` → ok) and `hci0` comes up; no `xhci ... not part of TD` in `dmesg`. See
*Findings* for the failure signature, it will recur.

**Fused scan, first real-hardware run (2026-07-31, CLI activity 3, ~45 s, 12 devices).**
The one claim that had never been observed is now confirmed: **`Conn` carries real
values** — 5 `YES`, 4 `no`, 3 `?` (`csv/fused_1785477305.csv`). The `?` are correct by
construction: devices BlueZ saw but the dongle did not in the same window, which is
exactly what the column is supposed to admit rather than guess.

**Then the recon stage was reworked** after reading `/opt/repo/Whisper_Bully` (the
maintainer's daily tool for this stage) — see the finding *"The target is induced, not
found"*. Two design errors were corrected: the target filter (Fast Pair, not address
kind) and the point where the disclosure is sampled (connection, not pairing). Changes:
- `scan_fused.target_note()` — `FP` / `RPA` / `FP+RPA`, new `Target` column, `V` now
  filters on *any* target signal (`only_targets`), CSV column `Viable` → `FastPairAdv`.
- `identity.extract()` — snapshots the device table **right after the connection** and
  **skips the pairing entirely** when that already disclosed; new verdict and footer for
  the connect-only path; candidate evidence `connection (RPA resolved)`, ranked just
  below a new bond.
- `cli.py` — the menu entry states which signal holds, and warns explicitly when a row
  is `RPA` without Fast Pair, i.e. reachable but of **unverified ownership**.
- Validated offline (synthetic records, both backends): FE2C decoding from both
  `bluetoothctl` spellings, all 8 classification cases, the filter, the `Target` column,
  and both report paths. Live: the fused table and CSV still render (30 s run, 6 devices).

**`Target = FP` confirmed on real hardware** (2026-07-31), without buying anything: the
dongle was made to *transmit* a Fast Pair advertisement (`0xFE2C` + 3-byte Model ID,
connectable) and the BlueZ scanner decoded it — `CE:C1:1B:51:DD:23 rnd-s FP Yes
FPTestBeacon 0xFE2C`. So the empty column across four sessions was an empty environment,
not a decoding bug — the doubt that could not be settled by scanning harder. The
harness is `fp_advertiser.py` in the scratchpad (not committed, per CLAUDE.md); it is
~60 lines over `blatann` and worth recreating rather than remembering.

## Test plan — validating the disclosure (EXECUTED 2026-08-28 → `docs/test-plan-redmi-buds-6.md`, result NEGATIVE; kept for the cold-run procedure)

The one claim the whole rework rests on is **unverified**: that the *connection alone*
discloses the identity address, before any pairing. Everything else is proven. This plan
is written to be runnable without re-deriving the reasoning — the session it came from
is gone.

**Hardware.** Ordered / to obtain: **Redmi Buds 6 Play** (~15-20 €). Google Fast Pair is
declared on Xiaomi's official spec page ("Quick pairing with Android devices by simply
opening the case", Google Play services 11.7+, Android 6.0+). Any Fast Pair accessory
does; a phone in "pair new device" does **not** — it is a Fast Pair *seeker*, not an
advertiser. Galaxy Buds+ are ruled out (see *Findings*).

**Entering pairing mode.** Opening the lid is enough while the buds are still unpaired.
Once they have been paired to something, the way back in is: buds in the case, lid open,
**hold the case button until the LED blinks rapidly**. (This is the step that never took
on the Buds+, which is why it is written down.)

**Run it in this order:**
1. **Before pairing them to anything.** Fresh out of the box they sit in pairing mode on
   their own — the easiest possible conditions, and they only happen once.
2. CLI activity 3 (fused scan). Keep the scan **short** (20-30 s) and the buds **a few
   cm from the box**: the record must be fresh (an RPA rotates) and strong (fusion drops
   `Conn` below ≈ −94 dBm, see *Findings*).
3. Read the `Target` column, then act on what it says:
   - **`FP+RPA`** → the ready case. Select the row, `i`, and answer the actual question:
     does the report say *"disclosed ... as soon as the LE connection completed"*
     (`skipped_pairing`, connect-only footer), or did it need the pairing fallback?
     Record which, verbatim.
   - **`FP` only** (Fast Pair but a static address) → *also a result, not a failure*.
     It would mean even a current Fast Pair accessory advertises no resolvable private
     address, and the technique's real target is narrower than Whisper_Bully's README
     implies. Write it in *Findings* with the address kind observed.
   - **nothing** → suspect our decoding, not the environment, and use the cross-check
     below before touching the code.
4. **Cross-check with the phone.** An Android phone (the OnePlus Nord) pops up the Fast
   Pair dialog for an accessory in pairing mode it has never been paired with. If the
   popup appears and our `Target` column stays empty, the defect is ours. Caveat that
   invalidated this test once already: the popup is **suppressed for a device already
   paired to that phone**, and needs Bluetooth + location + nearby-device scanning on.

**Harnesses to recreate** (scratchpad, never committed — per CLAUDE.md):
- `fp_advertiser.py` — ~60 lines over `blatann`: makes the dongle transmit `0xFE2C` +
  a 3-byte Model ID, connectable, so the scan side can be tested with no accessory at
  all. `service_uuid16s` wants `Uuid16` objects, `service_data` raw bytes. **Read it
  back with `scan_le.LeScanner` directly, never through the fused scanner** — the fused
  scanner tries to open the busy dongle and that attempt resets it, killing the
  advertising for reasons that look like a decoding bug.
- `drive_fused.py` / `drive_identity.py` — drive `cli.py` through a `pty` (activity 3 →
  `^S` save CSV → `V` filter → row select → `i`). Assert on the **CSV**, not on the
  scraped curses frame.

**Verification harnesses** are not committed (per CLAUDE.md) and live in the session
scratchpad. Recreate as needed: one drives `cli.py` through a `pty` (main menu →
activity 3 → keys → `^S` save CSV → `V` filter → `Q`/`q`; arrows are `\x1bOB`/`\x1bOA`,
and the **CSV is the reliable assertion target** — a curses frame scraped off the pty is
too mangled to parse), the other injects synthetic records into
`FusedScanner.dongle.devices` / `.bluez.devices` to prove the merge-by-address,
viability, filter, CSV and the three passive refusals without any hardware.

## TODO

- [ ] Optional: make the `csv/` export order match the on-screen discovery order
      (currently CSV is sorted by RSSI).
- [ ] Re-validate the SPP serial console end-to-end once the Jabra bond is restored
      (they dropped the box's bond by reconnecting to a phone); last live run of the
      full CLI path stopped at `br-connection-key-missing`.
- [x] ~~**Validate `identity.extract` against a real Fast Pair target**~~ — done
      2026-08-28 against a Redmi Buds 6 Play, **result NEGATIVE**: the disclosure does
      not reproduce (bond refused `AuthenticationCanceled`, no identity address in the
      table), and Whisper_Bully fails identically → not an intotheblue bug. Full run and
      the mechanism analysis in `docs/test-plan-redmi-buds-6.md` and *Resume here* above.
      Follow-up TODO added below: obtain a known-vulnerable target as a positive control.
- [x] ~~**Positive control for the disclosure pipeline**~~ — done 2026-08-28 with an
      **in-house** control: `emulate.py --positive-control` (dongle advertises an RPA +
      accepts a Just Works bond). `identity.extract` disclosed the identity
      (`CE:C1:1B:51:DD:23`) end-to-end → pipeline proven, Redmi negative is a device
      property. See *Resume here* → Caveat RESOLVED, and the actions log.
- [ ] *(optional)* Confirm on a **real-world** Fast Pair accessory that does not enforce
      the bond gate — nice-to-have; the in-house control already validates the pipeline.
- [x] ~~**Fused Model-ID masking** (`scan_fused.snapshot`)~~ — fixed 2026-08-28 (impl,
      not yet hardware-validated). `_merge_fast_pair()` now prefers a decoded Model ID
      over a bare `"Yes"` from either backend, instead of the old `bluez or dongle`.
      See the *Next steps* block and the actions log.
- [x] ~~**See the fused table with real `Conn` values**~~ — done 2026-07-31, first
      real-hardware run: 5 `YES` / 4 `no` / 3 `?` over 12 devices. See *Resume here*.
- [x] ~~**Find out whether a viable target exists at all in this environment**~~ —
      **the question was malformed**; closed 2026-07-31 after studying Whisper_Bully.
      A Fast Pair target only exists while someone holds a device in **pairing mode**,
      so scanning the ambient environment for one is structurally futile. The target is
      *induced*, not found. See the finding below; the four sessions spent on this were
      measuring the wrong thing, not measuring a narrow technique.
- [ ] ~~Consider surfacing connectability in the BlueZ table~~ — done via the fused
      scanner. Kept as a reminder of the alternative: an explicit "unknown", which is
      what the degraded (no-dongle) mode falls back to. It cannot be read from
      BlueZ (no PDU type), so the only honest options are a cross-check against the
      dongle scan or an explicit "unknown". Today the check just reports it after the
      fact in its verdict.

## Checkpoint

- 2026-07-22: user-validated checkpoint ("per adesso sembra ok"). Interactive Classic
  menu (Pair + navigable SDP profiles → SPP console / PBAP), plus the system-python
  fix for the RFCOMM/D-Bus helpers. Committed and pushed to `origin/devel`.

## Design decisions

- **Python 3.10 pinned** (`.venv310/`) — `pc-ble-driver-py` (native lib under blatann)
  ships no wheels for ≥ 3.11. Non-negotiable until that changes upstream.
- **Two hardware backends, kept separate** — nRF52 Connectivity dongle (serial, via
  blatann) for BLE; host BlueZ adapter (`bluetoothctl`) for Classic. Do not conflate.
- **Dongle stays open across an interaction** — pause the scan and connect on the same
  `BleDevice`. Closing then reopening immediately fails with
  `NrfError.rpc_h5_transport_state`; this drove the whole scan→pause→act→resume design.
- **Table keeps discovery order** (no live RSSI re-sort) so rows don't jump under the
  cursor during selection.
- **Shared `livetable` scanner interface** — new scan surfaces implement
  `snapshot/is_scanning/pause/resume/save_csv` instead of duplicating the curses UI.
- **Fusing the two BLE radios** (`scan_fused.py`) — they observe the same advertising
  packets at the same instant, so at any moment they report the *same* address, even a
  rotating private one: that makes them mergeable by address. Needed because the fact
  that decides whether the identity check is even attemptable (connectable, i.e. the
  advertising PDU type) is known **only to the dongle**, while the action can only run
  **through BlueZ**. Triaging on one table and acting from the other meant the address
  had rotated in between, so the verdict never applied to the device actually touched —
  every failed live run of 2026-07-30 traces back to this. The dongle stays optional:
  without it the table degrades to the BlueZ-only view with `Conn = ?`, and nothing is
  ever *claimed* viable on a guess.
- **Two independent target signals, never collapsed into one boolean**
  (`scan_fused.target_note`) — `FP` (advertises Fast Pair, i.e. in pairing mode:
  a target you may legitimately act on and can attribute by name) and `RPA`
  (`rnd-r` + connectable: the mechanism has something to unmask). They answer different
  questions, so the table shows which of the two holds rather than a single "viable"
  verdict that silently conflated *reachable* with *yours*. `is_viable()` keeps its exact
  old meaning and is now one input of the two.
- **Prefer connect-only over pairing** (`identity.extract`) — since the disclosure
  originates at connection completion, the pairing is attempted **only** when the
  connection produced nothing attributable (typically an RPA advertising no name, where a
  new bond is the only way to tie an address to the peer). When the connection alone
  discloses, no SMP runs and nothing is left on the target: the least intrusive path that
  still answers the question.
- **A third hardware path: BLE over BlueZ** (`scan_le.py`) — deliberate exception to
  "two backends, kept separate". Host-side actions (`bluetoothctl connect`/`pair`) only
  work on devices in **BlueZ's own** cache, and a device seen by the dongle is invisible
  to BlueZ, so the scan feeding those actions has to run on `hci0` too. Implemented as a
  subclass of `ClassicScanner` (the bluetoothctl event stream is transport-agnostic;
  only `scan le` vs `scan bredr` and the record decoding differ), not as a fourth copy
  of the scan loop. The dongle BLE path is untouched.
- **`connectable` is `None`, not `False`, on the BlueZ backend** — BlueZ does not
  surface the advertising PDU type. `exposure.assess` reports "unknown" for it rather
  than guessing: an unverified claim has no place in a security report.
- **Identity disclosure is attributed, never inferred** (`identity.py`) — the first live
  run showed why: while our own `scan le` runs, unrelated neighbours keep entering the
  device table, and a naive before/after diff reported three of them as "identity
  candidates" *and removed them from the host cache*. A new address now counts only if
  it carries the target's name or appears on a `Paired:`/`Bonded: yes` line, and cleanup
  only ever touches entries this run introduced (never anything present in the baseline
  — removing a bonded neighbour would destroy a legitimate bond).
- **Passive/active boundary** — passive analysis (`exposure.py`, record enrichment)
  must not transmit; anything that connects/writes sits behind an explicit user action.
  Heavy active primitives (e.g. L2CAP flooding) live in external, dedicated tooling.
- **English for code and all UI text.**

## Findings

- **The identity disclosure does not reproduce on a real Fast Pair target, and the
  reference tool fails the same way** (2026-08-28, Redmi Buds 6 Play; full run in
  `docs/test-plan-redmi-buds-6.md`). The host opens an unauthenticated LE connection
  and reads full GATT, but the Just Works bond is refused
  (`org.bluez.Error.AuthenticationCanceled`) and no permanent identity address enters
  the BlueZ table — virgin *and* phone-bonded. `Whisper_Bully` (`sudo` + `bleak`) fails
  identically (`Failed to extract`). Root cause: resolving an RPA needs the peer's IRK,
  distributed only on a **completed** bond; a host that has never bonded the peer has no
  IRK and cannot resolve the RPA at connection — so WB's README claim of resolution at
  `LL_CONNECTION_COMPLETE` (before SMP, even on patched devices) is empirically wrong
  here. Two operational lessons: (1) `identity.extract`'s name-based attribution is blind
  when the connection doesn't resolve a name (the reconnect RPA advertises none), so a
  leak that surfaced only as an unnamed table entry would be missed — a manual
  device-table check (as WB does) is the backstop; (2) the whole 2026-07-31 "prefer
  connect-only" rework was built on a misread of WB's mechanism — WB depends on the bond
  (or on the host already holding the IRK), it does not disclose from a bare connection.

- **Emulate the target to test the observer** (2026-07-31). The fastest way to prove a
  *scan-side* decoding path is to make the dongle transmit the advertisement we claim to
  decode, and read it back on the BlueZ side — the two backends are independent radios,
  so one can be the device under test for the other. This settled in minutes a question
  four evenings of ambient scanning could not. Its limit is equally clear: an emulated
  advertiser validates **observation**, never **disclosure**, since it has no secret to
  leak. Generalises well to the roadmap's cloning/emulation direction.
  Two practical constraints found doing it:
  - The dongle **cannot advertise and scan at the same time**. Worse, running the fused
    scan while it advertises makes `FusedScanner.open()` try to open the busy dongle,
    and that attempt *resets it* and kills the advertising — the beacon then vanishes
    from the table for a reason that has nothing to do with decoding. Test the scan-side
    with `scan_le.LeScanner` alone, not through the fused scanner.
  - `blatann`'s `AdvertisingData` wants `Uuid16` objects in `service_uuid16s` (an `int`
    raises `AttributeError: 'int' object has no attribute 'uuid'`); `service_data` is
    passed through as raw bytes.

- **`open_device`'s by-id autodetect earns its keep** (2026-07-31). Mid-session the
  dongle hit the `xhci_hcd ... not part of TD` fault again, disconnected and
  **re-enumerated as `ttyACM1`**. Everything kept working because the lookup goes through
  `/dev/serial/by-id/*nRF52*` rather than a hardcoded `ttyACM0`. So this fault is not
  always the session-ending kind documented below: it can self-recover, and the node
  number is not stable across it. Never hardcode the node.

- **Environment: VMware USB passthrough is a real failure mode** (2026-07-31). Symptoms:
  `NrfError.timeout` from `open_device` regardless of baud or reset, `EPROTO` opening
  `/dev/ttyACM0`, continuous `xhci_hcd ... not part of TD` in `dmesg`. A guest-side
  replug does not clear it; the host Bluetooth died too, forcing a reboot of both. The
  dongle and the host BT adapter share one virtual hub, so a single USB fault takes out
  **both** BLE backends at once. Check `dmesg | grep xhci` before debugging this code.
  Distinct from the documented transient `rpc_h5_transport_state` between back-to-back
  sessions, which idle time does cure.

- **You cannot attribute an RPA to an owner before acting on it** (2026-07-31) — the
  operational sting in the tail of the entry below. Selecting "the first viable row"
  is *not* the same as selecting your own device: the address rotates, so the row that
  is viable now cannot be matched against the row you identified as yours a minute ago.
  In the first full live run the check was aimed at `5E:09:2A:E7:2D:87` with no way to
  say whose it was. **Working procedure**: bring the authorised device to a few
  centimetres of the box and pick the viable row by **overwhelming RSSI** (≈ −40 against
  neighbours at −88…−100). Proximity is the only attribution signal available before a
  connection, and it has to be arranged deliberately, not hoped for.

- **The liveness probe earns its keep** (2026-07-31, first live run of the full active
  path). Verdict: *"Target advertising NO — not seen during the run; the private address
  had already rotated"*, with `Connection failed — not available` and
  `AuthenticationCanceled` downstream of it. Without the probe this is precisely the
  silent, indistinguishable failure that cost a minute per attempt on 2026-07-30 — the
  report now names the cause and tells the operator to reselect a row with a low `Age`
  instead of leaving them to guess. Corollary for the workflow: **keep the scan short
  before acting**; a 50 s scan means the selected record can already be 50 s stale, and
  an RPA does not wait.

- **`is_viable()` is a reachability test, not an authorisation test** (2026-07-31). The
  first viable row this project has ever produced — `7B:42:23:C5:AC:82`, `rnd-r` +
  connectable, Apple, −88 dBm — is an **unidentified neighbouring device**, not bench
  equipment. That is the expected case, not an accident: in a non-isolated environment
  the devices with a *rotating* address are overwhelmingly other people's phones,
  watches and earbuds, so the filter that finds a technically attackable target will
  usually find a stranger's. Viability must therefore never auto-arm the active check;
  the operator has to establish ownership first, and the menu entry stays the explicit
  gate. Worth stating in the report wording too: "viable" means *reachable*, nothing
  more.

- **Galaxy Buds+ are not usable for this check** (measured 2026-07-31, three fused runs
  + one BR/EDR inquiry). They advertise a **static** random address that was byte-for-byte
  identical across runs 20 minutes apart (`F7:18:2D:72:11:86` connectable, plus a
  non-connectable `F4:9D:80:C5:48:8D` "Buds+"), emit **no Fast Pair** service (`0xFE2C`)
  and never became BR/EDR discoverable, i.e. the pairing-mode sequence never took even
  when driven during a live scan. A Fast Pair *model* does not imply Fast Pair
  *advertising* in the state you find the device in. The RSSI rose (−88 → −76 → −80) so
  the radio was fine; the device simply had nothing else to say.

- **Fusion degrades at the edge of range, and that is where the interesting targets are**
  (2026-07-31). Merging by address only works for a device *both* radios happen to hear;
  all 3 `?` rows of the first live run sat at RSSI ≤ −94, including the run's only
  `rnd-r`. So the weakest signals — precisely the ones a rotating-address accessory tends
  to produce — are the likeliest to lose their `Conn`. Consequences: never read `?` as
  "not connectable" (the code already doesn't), and when triaging a specific target, get
  closer to it rather than scanning longer from the same spot.

- **Phones are not a target for the identity-disclosure check** (measured 2026-07-30 on a
  OnePlus Nord). In "pair new device" a phone is discoverable over **BR/EDR**; its LE
  advertising is Google Nearby (service `FEF3`) and the dongle scan shows it as
  `conn=no` — **non-connectable**. No LE connection ⇒ no SMP ⇒ nothing to distribute;
  BlueZ returns `org.bluez.Error.ConnectionAttemptFailed`, which is correct behaviour,
  not a defect. The target class is accessories advertising a **connectable** RPA that
  accept Just Works (Fast Pair earbuds, trackers, wearables).
- **The target is induced, not found — and the leak is at the connection, not the
  pairing** (2026-07-31, from reading `/opt/repo/Whisper_Bully`, the tool the maintainer
  uses daily for this stage). Two corrections to assumptions that had cost four
  sessions:
  1. **Target selection.** Whisper_Bully filters candidates on **Fast Pair service
     `0xFE2C`** and never looks at the address kind; its README says the UUID is used
     *"only as a scan filter to identify candidate targets"*. A device emits discoverable
     Fast Pair advertising **only while in pairing mode**, a state a human has to induce
     — which is also what makes the target *attributable*, by name. Our `rnd-r +
     connectable` filter describes where the mechanism works, but as a search strategy it
     selects, by construction, strangers' phones and watches: an RPA can never be tied to
     an owner by looking at it. Hence the earlier "no viable target in the environment"
     and "the technique's reach is narrow" — both were artefacts of the wrong filter, not
     results.
  2. **Where the disclosure happens.** BlueZ resolves the peer's RPA and caches the
     identity address while handling **connection completion**, at HCI level, before any
     SMP or GATT interaction. The pairing is a *fallback*, not the mechanism. Our check
     sampled the device table only after the pairing, so a failed pairing (the common
     outcome) could hide a leak that had already happened — while still paying the cost
     of an intrusive bond attempt.
  The old profile facts remain true and still drive the passive refusals: `pub`/`rnd-s`
  have no private address to unmask (a *static* random address is itself a permanent
  identity — Core spec Vol 3 Part C 10.8) and a non-connectable advertiser is unreachable
  by any connection-based check.
- **Corollary — the two BLE backends are complementary in practice**: the dongle scan is
  the only one that reports the advertising PDU type, so it is the right place to triage
  whether a target is reachable at all; the BlueZ scan is where the host-side action
  runs. That is worth keeping in mind before blaming the check for a silent failure.

## Goals (standing)

- **Identify as many paired Bluetooth devices as possible** (stated 2026-08-28). The
  negative identity-disclosure result on the Redmi Buds 6 Play retires neither the goal
  nor the direction: attributing devices and their pairing relationships stays a core
  recon objective. The connection-based RPA unmask is *one* avenue that did not work on
  a current accessory — pursue the goal by whatever fits scan/select/act (advertising,
  GATT, Classic SDP/CoD, proximity, and a future known-vulnerable positive control).

## Next steps (prioritised) — 2026-08-28

Identity-disclosure stage is closed/validated (pipeline works; hardened Fast Pair
devices refuse the bond, so as an unmask technique its reach is narrow). The standing
goal (*identify as many paired BT devices as possible*) is best served by other avenues.
Options, best value/effort first — **currently starting (1)**:

1. **Passive identification enrichment** (low effort, high value, serves the goal now):
   *2026-08-28 — four items landed and were **hardware-validated** against the buds in
   discoverable pairing mode (Model ID `FB9B72` captured live). See the actions log.*
   - [x] **Fast Pair Model ID → product name** — new `bledev/fastpair.py` resolves the
     24-bit Model ID against a **local** `fastpair_models.yaml` (mirrors the
     `manufact.yaml`/`manufacturers.py` pattern; chosen over Google's live
     `nearbydevices-pa` endpoint to keep the bench offline/non-phoning-home). The
     FastPair column (scan_live + fused) prints the product name when the Model ID is
     known, else the raw hex / `"Yes"`. Registry seeded from the live capture:
     `0xFB9B72 → Redmi Buds 6 Play` (decoded by the dongle, attributed by the
     maintainer). Grows from real captures — see the file header for the procedure.
   - [x] **Fix fused Model-ID masking** (`scan_fused._merge_fast_pair`) — a decoded Model
     ID now wins over a bare `"Yes"`; closed the TODO. **Validated live**: fused merge of
     the RPA `4C:96:7E:68:F4:36` kept `FB9B72` instead of BlueZ's `"Yes"`, `target=FP+RPA`.
   - [x] **Service-UUID → SIG name** — new `SvcNames` column in the **dongle** scan
     (`scan_live`); records accumulate `service_names`. (Empty on pure FP discoverable
     frames — they carry only FE2C service-data, no service-UUID list; expected.)
   - [x] **Authoritative SIG data** (`bledev/sig.py`, from the `BluetoothSIGPublic/`
     checkout the maintainer added) — company IDs (4018, up from manufact.yaml's 3877),
     vendor **member** UUIDs (0xFE2C→Google …), service/characteristic names. Wired as
     the *primary* source with graceful fallback in `manufacturers.get_manufacturer`,
     `scan_live._service_name`/`_decode_protocol`, and `scan_le._add_uuid`. The checkout
     is git-ignored (nested repo). Curated short labels still win over the raw registered
     company name (e.g. "Google FastPair" over "Google LLC").
   - [x] **OUI vendor lookup** for public addresses — done 2026-08-28. New top-level
     `oui.py` resolves the manufacturer from a public MAC's IEEE OUI (longest prefix: MA-S
     36-bit → MA-M 28-bit → MA-L 24-bit) using the system `ieee-data` CSVs, nmap fallback.
     Random BLE addresses are **never** looked up (no OUI). Surfaced as a `Vendor` column
     in `scan_classic` (BR/EDR MACs are always public) and a `Vendor (OUI)` row in the
     passive `exposure` report for public BLE addresses. Live-validated on a real Classic
     device (`00:1D:DF:7E:FB:44` "PHILIPS HTL2110" → OUI *Sunitec Enterprise Co.,Ltd*).
   - [x] **TX Power** — done 2026-08-28. `scan_live._tx_power` reads the Tx Power Level AD
     (0x0A) from blatann's parsed `entries` (stored as raw bytes on the receive path) and
     converts to a signed dBm. New `tx_power` field + `TxPwr` column in the dongle scan;
     merged onto the fused/BlueZ record; a `Tx Power` row in the `exposure` report shows a
     path-loss (proximity) estimate `tx_power − rssi`. Live-validated on a real advertiser
     (`46:0C:50:50:4D:61`, −8 dBm, RSSI −23 → path loss ≈ 15 dB).

   **Item 1 is complete as advertising-based enrichment** — all sub-items landed and were
   hardware-validated. **This does NOT reach the standing goal** (see below): every one of
   these signals is derived from *advertising*, so none of them can see a device that is in
   an active connection to a phone and therefore not advertising.

## The goal is NOT reached — phone-connected devices are invisible to passive scanning (2026-08-28)

The maintainer's standing goal is *identify as many paired Bluetooth devices as possible*.
Two concrete targets we cannot currently identify, and why:

- **Redmi Buds 6 Play ↔ the maintainer's Android** (BLE / Fast Pair): while connected the
  buds stop discoverable advertising; at most they emit brief RPA reconnection frames with
  an empty account key filter (see item 2). A passive advertising scanner cannot attribute
  or even reliably see them mid-connection.
- **JBL Go 3 ↔ the maintainer's iPhone** (BR/EDR / Classic): while connected it holds an
  active ACL link and is **not** in inquiry scan, so `scan_classic` (bluetoothctl inquiry)
  never sees it. Classic has no advertising to fall back on.

**Root cause.** All of intotheblue's identification is *advertising/inquiry* based. A device
already bonded to and connected with a phone communicates over a point-to-point link on
hopping data channels and does not advertise — so the thing we most want to enumerate is
exactly the thing this whole approach cannot observe.

**What could observe them — connection *sniffing*, not scanning** (researched 2026-08-28):
- **BLE**: follow the connection. A sniffer catches the phone's `CONNECT_IND` (carries both
  addresses, access address, channel map, hop increment) and then follows the hopping link,
  capturing the data-channel packets — so the device becomes visible *while connected to the
  phone*. Best-in-class tool: **Sniffle** (nccgroup). **Corrected hardware fact:** current
  Sniffle (master) runs **only on TI CC1352/CC26x2**, NOT the nRF52840 — the README has zero
  Nordic mentions; nRF52840 support existed only in *old* Sniffle releases (unmaintained).
  So:
    - **Sniffle never supported nRF52840** — checked every tag v1.0–v1.11.0, zero Nordic
      mentions (an earlier note of mine claiming an "old nRF52840 Sniffle build" was wrong).
    - the cheap maintained path is a **TI CC2652P** board — the **SONOFF ZBDongle-P
      (~€20-25)**. ⚠️ **Buy the right variant**: SONOFF's "Zigbee 3.0 USB Dongle Plus" ships
      as **ZBDongle-P = CC2652P (TI)** [works] *and* **ZBDongle-E = EFR32MG21 (Silicon Labs)**
      [does NOT work] — only the **-P** works with Sniffle. Prebuilt firmware exists (verified: release
      v1.11.0 asset `sniffle_cc1352p1_cc2652p1.hex`), so no compiling. Flash via
      `cc2538-bsl.py --bootloader-sonoff-usb` (chip is a CP2102N → /dev/ttyUSB0); use the
      `sultanqasim/cc2538-bsl` fork / PR #173 for a known post-flash reset bug, and the
      `_1M.hex` variant for some 2022-era dongles. Keep the nRF52840 for blatann. Sniffle
      scans all 3 advertising channels at once → ~3× more reliable connection detection.
    - **ZigStar GW (LAN+USB) — hardware-probed 2026-09-15; NOT usable for Sniffle (see FINAL VERDICT).** Right
      radio (**CC2652P**, module E72-2G4M20S1E / RF-BM-2652P2), and it IS connected by USB
      (a **CH340 → /dev/ttyUSB0**; an earlier "LAN-only" claim of mine was wrong). But the
      CH340 does **not** reach the CC2652P directly: probed `ttyUSB0` and got **no Z-Stack MT
      SYS_PING reply** (115200/230400/460800/2000000) and **no CC26xx serial-bootloader sync**
      (cc2538-bsl, both `--bootloader-sonoff-usb` and `--bootloader-invert-lines`: "Timeout
      waiting for ACK"). The CH340 goes to the **ESP32 (WT32-ETH01)**; the CC2652P sits
      *behind* it (RST/BSL on ESP32 GPIOs zRST/zBSL, UART on GPIO5/17), reached only *through*
      the ESP32 — the official flash path is `cc2538-bsl.py -p socket://ip:6638` over the
      ESP32's TCP stream_server (115200). **Architectural mismatch:** Sniffle needs the
      CC2652P on a **direct** USB-UART at **2 Mbps** (the SONOFF ZBDongle-P wires CC2652P
      straight to a CP2102N — no MCU between). On the ZigStar the CC2652P is always behind the
      ESP32 bridge (USB *or* LAN), so Sniffle's direct-serial model doesn't fit and 2 Mbps
      through the ESP32 is the same bottleneck either way.
      **FINAL VERDICT (same day, verified — not usable).** Chased the "USB mode" hard: set
      both DIP switches ON (docs say USB Coordinator mode, "without connection to
      WT32-ETH01"), and the maintainer put the CC2652P in BSL with the physical buttons.
      Still **no CC2652P response on `ttyUSB0`** — Z-Stack MT ping silent at every baud, and
      cc2538-bsl never synced (every entry polarity: sonoff-usb / invert-lines / active-high /
      plain, 115200–921600). The decider: the **vendor's own ZigStar Multi Tool** flashes the
      LAN GW's CC2652P **over the network (IP:PORT), never over USB serial** — its README
      lists the LAN GW under "Network gateways (enter IP:PORT)", separate from "USB sticks".
      So on the LAN GW the CC2652P is **always behind the ESP32** (BSL/RST on its GPIOs,
      UART bridged over TCP); the CH340 is the ESP32's port. There is **no direct
      CH340→CC2652P path**, whatever the DIP switches do. Sniffle needs the CC2652P on a
      **direct local USB-UART at 2 Mbps** (the SONOFF ZBDongle-P wires CC2652P straight to a
      CP2102N — no MCU between); the ZigStar can't provide that. **Conclusion: not usable for
      Sniffle** — set the DIP switches back to LAN (OFF) and power-cycle to restore the
      gateway.
      **Two paths that could still use the ZigStar (maintainer's idea, 2026-09-15):**
      (A) *Sniffle over the ESP32 TCP bridge.* Flashing is not the blocker — the vendor path
      `cc2538-bsl.py -p socket://IP:6638 -evw sniffle_cc1352p1_cc2652p1.hex` flashes Sniffle
      onto the CC2652P through the ESP32, no Sniffle change needed. To *run* it, patch
      `SniffleHW.__init__` to use `serial.serial_for_url('socket://IP:6638')` instead of
      `Serial(port)` (pyserial supports socket://; ~2 lines). Remaining blockers: the ESP32's
      bridge UART must run at Sniffle's **2 Mbps** (reconfigure ESPHome `uart: baud_rate:
      2000000` and reflash the ESP32 — not the nRF), and the ESP32/Ethernet must sustain
      2 Mbps of connection-following bursts with low enough latency for Sniffle's protocol —
      **unproven**, could drop packets / miss the CONNECT_IND. A pure-software experiment
      worth trying (no new parts). (B) *External UART on the exposed CC2652P pins* — the board
      breaks out all pins; wire a **3.3 V USB-UART** adapter straight to the CC2652P module's
      TX/RX/GND (BSL entry via the onboard buttons), bypassing BOTH the ESP32 and the CH340.
      That gives a true direct 2 Mbps serial → unmodified Sniffle, no bottleneck. Needs a
      cheap USB-UART adapter (~€2) + the module UART pinout from the board schematic. Most
      reliable revival. Fallback if neither pans out: SONOFF ZBDongle-P (CC2652P direct on a
      CP2102N).
      **EXECUTED Approach B (2026-09-15) — Sniffle flashed & running; blocked only by the
      adapter's baud ceiling.** The maintainer wired an external USB-UART to the CC2652P
      TX/RX/GND. The chip answered a **Z-Stack MT SYS_PING** (`fe 02 61 01 59 06 3d`) at
      115200 → the UART reaches the radio directly (ESP32 fully bypassed; the onboard CH340
      never did). BSL entry: the buttons alone don't reset the CC2652P (its RESET is an ESP32
      GPIO), so **hold BSL + power-cycle the module + release BSL** enters the ROM bootloader.
      cc2538-bsl then read the chip (352 KB flash, IEEE `00:12:4B:…` = TI; it mislabels it
      "CC1350") and **flashed `sniffle_cc1352p1_cc2652p1.hex`, CRC-verified**. Sniffle now
      runs and streams messages — BUT the host decodes only **garbage** (base64 padding
      errors). Root cause: the adapter is a **classic CP2102 (max ~1 Mbaud)** and Sniffle's
      UART is **fixed at 2 Mbps**; the ROM bootloader auto-bauds so flashing worked at ≤1 M
      and masked the ceiling, but Sniffle's fixed 2 M output the CP2102 can't sample →
      framing corruption. **Fix = a 2 Mbps-capable USB-UART on the same 3 wires: CH340 (~€1,
      does 2 M), FTDI FT232 (3 M), or a CP2102*N* (3 M) — NOT a classic CP2102.** Then the
      free ZigStar path is complete. (Zero-purchase alternative: rebuild Sniffle firmware
      with the UART `#define` at 1 M — needs the TI SimpleLink SDK + arm-none-eabi toolchain,
      heavy; a €1 CH340 is far simpler.) Z-Stack was erased by the flash; to restore the
      gateway's Zigbee, reflash a coordinator hex via the same BSL method.
      **Adapter ordered (2026-09-15):** DSD TECH **SH-U09C5** (FTDI **FT232RNL**, 3 Mbps max
      — 2 M with headroom, cleaner than CH340; verified from the FTDI datasheet). Chosen over
      the SH-U09C2 because it breaks out **DTR/RTS** (and RESET), enabling **automatic BSL
      entry** — no more manual hold-BSL + power-cycle. **MUST set VCC/TTL to 3.3 V** (the
      CC2652P is 3.3 V; 5 V damages it). Wiring: data = adapter TXD→CC2652P RX, RXD→TX,
      GND→GND; auto-BSL = DTR→CC2652P **RESET**, RTS→CC2652P **BSL** (then cc2538-bsl
      `--bootloader-sonoff-usb`/right polarity auto-enters BSL; also makes restoring Z-Stack
      easy). Can power the module from the adapter's 3.3 V VCC (250 mA, ample). TODO when
      wiring: get the E72 module RESET and BSL(-backdoor DIO) pins from the ZigStar schematic.
      Sniffle is already flashed, so once wired at 3.3 V it should decode clean at 2 M — then
      build the `SniffleScanner`.
    - **Honest capability limit (calibrate before buying):** following reveals the
      *connection and its endpoints* (phone ↔ device = the pairing relationship) and makes
      the device observable on-air while connected — but a **bonded device's reconnection is
      LESC-encrypted (ECDH)**, which Sniffle cannot decrypt even by capturing the pairing.
      So we get the relationship, addresses (possibly RPA), timing and packet structure — not
      the GATT payload. For "who is paired with whom" that is still the key win.
    - free "try before buy" on the **existing nRF52840**: Nordic's **nRF Sniffer for
      Bluetooth LE** *does* follow a selected device's connection (packets between it and its
      peer, even encrypted) via a Wireshark extcap — good enough for a feasibility test that
      we can see the Redmi↔Android link. Trade-offs vs Sniffle: single advertising channel
      (less reliable at catching the CONNECT_IND, retry the reconnection), Wireshark-bound
      (harder to integrate than a Python CLI), and it displaces blatann on the one dongle.
  Host side: `sniff_receiver.py` (Python + PySerial over CDC-ACM), `-m MAC` filter, follows
  connections by default, PCAP export, `-i IRK` / `-Q` for RPA. **Operational limit:** it can
  only follow a connection whose *establishment* it observes (needs the CONNECT_IND) — it
  cannot join an already-running link, so we trigger a reconnection (buds off/on, BT toggle)
  while sniffing on all 3 advertising channels. RPAs may remain, so identity attribution
  stays partly open, but the device at least becomes observable.
- **Classic (BR/EDR)**: much harder — needs **Ubertooth One** (or similar) to recover the
  piconet LAP/UAP and follow the hop; the nRF52 cannot do BR/EDR at all. The JBL Go 3 case
  is blocked on hardware we do not have.

### Sniffle integration design (sketch, 2026-08-28 — studied the master python_cli)

Integrate the maintained **Sniffle Python package directly** (don't shell out to
`sniff_receiver.py`): vendor `python_cli/sniffle/` into the repo (or add as a dep). It is a
clean library — `sniffle.sniffle_hw.SniffleHW` + `sniffle.packet_decoder`. Python 3.9+
(our 3.10 is fine); needs **pyserial** (NOT currently installed — new requirement; blatann
uses its own serial, so this is additive).

- **Third, independent backend.** The TI CC2652P dongle is its own serial device, unrelated
  to the nRF52/blatann controller and to BlueZ/hci0 — like `scan_classic` wraps bluetoothctl,
  a new top-level `sniff.py` wraps SniffleHW. Autodetect its port the way `bledev` does.
- **API path:** `hw = SniffleHW(port)`; `hw.setup_sniffer(mode=SnifferMode.CONN_FOLLOW,
  targ_mac=<bytes>|targ_irk=<bytes>, hop3=True, rssi_min=…)`; `hw.mark_and_flush()`; then a
  thread loops `msg = hw.recv_and_decode()`. Decoded types that matter:
  `ConnectIndMessage` (**the relationship** — InitA=phone ↔ AdvA=device, access address,
  channel map, hop), `AdvIndMessage`/`ScanRspMessage` (advertising), and `DataMessage` /
  `LlControlMessage` (the **followed connection's** data-channel traffic → ATT/GATT, i.e.
  the device's name/services revealed *while it is connected to the phone*).
- **`SniffleScanner`** implements the `livetable` scanner interface (snapshot / is_scanning /
  pause / resume / save_csv) so it drops into the existing UI as a new activity, e.g.
  "BLE connection sniff". Record model: key by observed **connection** — show the two peers
  (phone ↔ device) and enrich the device row from the followed link (name, services, RPA vs
  identity). This is the first capability that surfaces a *phone↔device* pairing directly.
- **Operational workflow (must be in the UI copy):** following needs the `CONNECT_IND`, so
  start the sniffer then **trigger a reconnection** (device off/on, or toggle BT). For an
  **RPA** device (the Redmi buds), filter by **IRK** if known (we don't have it) — otherwise
  run with no MAC filter and catch every `CONNECT_IND`, then filter/attribute after.
- **select/act:** PCAP export (Sniffle has `sniffle.pcap`), dump the captured GATT, correlate
  the two peers. Later: `-i IRK` resolving if we ever obtain a device/phone IRK.

**Next decision (open):** acquire a TI CC2652P (SONOFF dongle) and integrate Sniffle
connection-following into the scan/select/act model (wrap `sniff_receiver.py` like
`scan_classic` wraps bluetoothctl) as the path to seeing phone-connected BLE devices; decide
separately whether Classic sniffing (Ubertooth) is worth the hardware. User is willing to buy
hardware. Until one lands, the goal stays partially open.
2. **Bond / pairing-relationship enumeration** (heart of the goal; research + new decoder).
   *2026-08-28 — implemented and **live-validated** on a real non-discoverable frame
   (buds bonded to a phone, out of pairing mode: FE2C payload `0000` → correctly decoded
   "not paired — empty account key filter"). Finding: a plain Bluetooth bond does **not**
   write a Fast Pair account key — a non-empty filter needs the device saved to a Google
   account (account-key flow). **Conclusive:** even after a full forget + re-pair via the
   phone's Fast Pair half-sheet, the buds advertised `0000` across three distinct
   addresses and the whole discoverable↔non-discoverable lifecycle — the Redmi Buds 6 Play
   simply do not populate a Fast Pair account key filter (consistent with the prior
   NEGATIVE identity-disclosure result: a minimal/hardened Fast Pair accessory). The
   non-empty/"bonded" branch is therefore validated on synthetic frames only.* New
   passive `bond.py` decodes what advertising reveals about bonds — **existence and
   count, never identity** (the account key filter is a one-way Bloom filter, reconnect
   addresses are RPAs):
   - **Fast Pair non-discoverable account key filter** (0xFE2C service data): empty →
     not paired to any account; non-empty → bonded to ≥1 account/seeker, filter grows
     with the number of keys; plus the show/hide reconnection UI flag. Frame layout and
     type nibbles from the Google "Provider advertising signal" spec (fetch of
     developers.google.com is blocked here — captured in `bond.py`'s docstring). Only
     decodable on the **dongle** path (raw service-data); the BlueZ path says so.
   - **Directed advertising** (`connectable_directed` / ADV_DIRECT_IND): reconnecting to
     a specific bonded peer. `scan_live` now captures a sticky `directed` flag.
   - **Host-side bond**: BlueZ Paired/Bonded (already captured as `paired`).
   Wired into `exposure.report` (both BLE and fused menus). `scan_live.DeviceRecord`
   gained `fp_payload`/`directed`; `scan_fused` merges them onto the actionable BlueZ
   record. **To validate live:** pair the buds to a phone, take them *out* of pairing
   mode so they advertise non-discoverable FE2C, and run the exposure check — the account
   key filter should read "bonded".
3. **Cloning / emulation** (bigger roadmap arc). *2026-08-28 — first extension done and
   live-validated.* `emulate.py --fast-pair MODELID` clones a real Fast Pair discoverable
   advertisement: Service Data (0x16) under 0xFE2C carrying the exact 3-byte Model ID,
   plus FE2C in the 16-bit UUID list, connectable, **nameless** (a real FP device is
   nameless; the seeker resolves the name from the Model ID via Google's registry). Raw
   AD `02010606162cfefb9b7203032cfe` for `fb9b72`. Validated: offline round-trip through
   `scan_live._fast_pair` → `FB9B72`; on air via an independent receiver (BlueZ/hci0 saw
   `CE:C1:1B:51:DD:23 FE2C "Google Fast Pair"`); the phone discovered it and let a connect
   attempt start. **Finding:** the phone showed no Fast Pair half-sheet because it already
   owns this model (bonded to the real buds) — Fast Pair only offers the half-sheet for a
   model you do *not* already own; validating the half-sheet needs a seeker without the
   Redmi buds. Clones only the *advertisement* — the FP GATT (anti-spoofing key exchange)
   is not implemented, so pairing does not complete. Next: implement the FE2C GATT for a
   fuller emulation; **clone from a live capture** (wire the scan's decoded Model ID
   straight into `--fast-pair`).
4. *(optional)* A real-world **known-vulnerable Fast Pair accessory** as a positive control
   on real hardware — nice-to-have; the in-house control already validates the pipeline.

## Open questions — to study / evaluate

- ~~**OUI vendor lookup** for public (non-random) addresses~~ — DONE 2026-08-28 (`oui.py`,
  system `ieee-data` CSVs). See next-steps item 1.
- **Service-UUID → SIG name** in the scan table (blatann has assigned-number tables;
  already used for GATT names in `client.py`, not yet surfaced in the scan columns).
- ~~**TX Power** is not exposed directly by blatann~~ — DONE 2026-08-28: read from the
  parsed AD `entries` (type 0x0A, raw bytes → signed dBm). See `scan_live._tx_power`.
- **Class of Device minor** decoding covers only common majors (`scan_classic._MINOR`);
  extend as needed.
- **Roadmap depth**: active enumeration, richer peripheral/server interaction, and
  device cloning/emulation — all still to be designed into the scan/select/act model.

## Actions log (recent first)

- **TX Power parsing — next-steps item 1 (now complete)** (2026-08-28, impl + live-validated).
  `scan_live._tx_power` reads the Tx Power Level AD (0x0A) from blatann's parsed `entries`
  (blatann exposes no property; on the receive path the value is raw bytes) and converts
  to a signed dBm. New `tx_power` field + `TxPwr` column (after RSSI) in the dongle scan;
  the field was added to `LeRecord` and merged in `scan_fused`; `exposure.report` gained a
  `Tx Power` row with a path-loss proximity estimate (`tx_power − rssi`). Offline: −59/+4/
  None via the real `from_ble_adv_records` path. Live: a real advertiser `46:0C:50:50:4D:61`
  reported −8 dBm, RSSI −23 → path loss ≈ 15 dB. This closes enrichment item 1 entirely
  (Model ID names, masking fix, SvcNames, SIG data, OUI, TX Power).

- **OUI vendor lookup — next-steps item 1, last enrichment sub-item** (2026-08-28, impl +
  live-validated). New top-level `oui.py` (like `exposure.py`/`bond.py`, no blatann import)
  resolves a manufacturer from a public MAC's IEEE OUI, longest prefix first (MA-S 36-bit →
  MA-M 28-bit → MA-L 24-bit) from the system `ieee-data` CSVs (/var/lib/ieee-data/*.csv,
  ~53k entries), nmap `nmap-mac-prefixes` fallback, None otherwise. Random BLE addresses
  are guarded out (`lookup(..., random=True) → None`) — they carry no OUI. Wired as a
  `Vendor` column in `scan_classic` and a `Vendor (OUI)` row in `exposure.report` for public
  BLE addresses. Offline: TP-Link (E8:48:B8), Nokia Shanghai Bell (28:6F:B9), random guard.
  Live: a real Classic device `00:1D:DF:7E:FB:44` "PHILIPS HTL2110" resolved to its OUI
  owner *Sunitec Enterprise Co.,Ltd* — the ODM behind the brand name. Leaves only TX Power
  open in next-steps item 1.

- **Fast Pair advertisement clone — next-steps item 3** (2026-08-28, impl + live-validated).
  New `emulate.py --fast-pair MODELID`: the dongle emits a faithful, nameless Fast Pair
  discoverable advertisement (FE2C Service Data + Model ID + FE2C in the 16-bit UUID list,
  connectable), folding the old scratchpad `fp_advertiser.py` into a first-class mode.
  Mutually exclusive with `--positive-control`; validates the 6-hex Model ID. Offline:
  round-trips through `scan_live._fast_pair` to the Model ID, serializes to
  `02010606162cfefb9b7203032cfe`. Live: BlueZ/hci0 (independent receiver) saw the dongle
  advertising FE2C while the clone ran; the phone discovered it. Half-sheet did not show —
  the phone already owns this model (bonded to the real buds); Fast Pair only offers to
  pair a model you don't already have. Advertisement-only (no FP GATT), so pairing can't
  complete. Made the clone nameless after first seeing the phone list it by the scan-
  response name, which masks the registry-driven FP recognition.

- **Bond / pairing-relationship enumeration — next-steps item 2** (2026-08-28, impl +
  offline-validated, live validation of the account key filter pending). New passive
  `bond.py`: decodes the Fast Pair non-discoverable **account key filter** (empty → not
  paired; non-empty → bonded to ≥1 account/seeker, + show/hide reconnection UI), flags
  **directed reconnect advertising**, and surfaces the **host-side bond**. Reports
  existence/count of bonds, never identity (one-way Bloom filter, RPA peers). Captured
  `fp_payload` + sticky `directed` in `scan_live.DeviceRecord`, added the same fields to
  `LeRecord` and merged them in `scan_fused.snapshot`, and wired `bond.signals` into
  `exposure.report` (reachable from both the BLE and fused per-device menus). Frame layout
  from the Google "Provider advertising signal" spec (primary-source fetch blocked in this
  env; type nibbles 0b0000 show-UI / 0b0010 hide-UI confirmed via search). Unit-tested the
  decoder on synthetic frames (discoverable/empty/bonded-show/bonded-hide/length-overflow)
  and the report on synthetic dongle + BlueZ records. **Live-validated 2026-08-28**: with
  the buds bonded to a phone and out of pairing mode, the dongle captured their
  non-discoverable FE2C frame `0000` (RPA `4C:96:7E:68:F4:36`, `directed=False`) and the
  decoder read it correctly as an **empty account key filter → not paired to an account**.
  Finding: normal Bluetooth pairing does not populate a Fast Pair account key; a non-empty
  filter requires the Fast Pair account-key flow (save to a Google account). Pushed further
  (3 more captures incl. a full forget + re-pair via the Fast Pair half-sheet): the buds
  stayed `0000` across three addresses and the whole discoverable↔non-discoverable
  lifecycle — **conclusively** these buds never populate the account key filter (minimal
  Fast Pair, as in the earlier NEGATIVE test), so the non-empty/"bonded" render stays
  synthetic-only here. Refined the empty-filter note (drop the meaningless reconnection-UI
  nibble when no filter is present).

- **Passive identification enrichment — next-steps item 1, hardware-validated + SIG data**
  (2026-08-28). Delivered and validated live against the Redmi Buds 6 Play in discoverable
  pairing mode. New `bledev/fastpair.py` + `fastpair_models.yaml` resolve a 24-bit Fast
  Pair Model ID → product name from a **local** registry (design fork resolved with the
  maintainer: local YAML over Google's live `nearbydevices-pa` API, to keep the bench
  offline/non-phoning-home). Wired into the FastPair column of `scan_live`/`scan_fused`
  (widened to 16, name-or-hex). Fixed the fused Model-ID masking (`_merge_fast_pair`: a
  Model ID beats a bare `"Yes"`). Added a `SvcNames` column to the dongle scan naming
  advertised service UUIDs. Then wired the **authoritative SIG dataset** the maintainer
  checked out at `BluetoothSIGPublic/`: new `bledev/sig.py` loads company IDs (4018),
  member/service/characteristic UUIDs, made the *primary* source (graceful fallback to the
  bundled snapshot / blatann map) in `manufacturers`, `scan_live` and `scan_le`; checkout
  git-ignored. **Live results** (dongle scan, `csv/scan_1787922603.csv`; fused,
  `csv/fused_1787922685.csv`): Model ID `FB9B72` decoded, `type=Google FastPair`; fused
  merge kept `FB9B72` over BlueZ `"Yes"` (`target=FP+RPA`, RPA `4C:96:7E:68:F4:36`).
  Attributed `FB9B72 → Redmi Buds 6 Play` and seeded the registry; the FastPair column now
  prints the product name. Offline checks also passed (synthetic ScanReport/AdvertisingData
  fakes: column output, non-colliding CSV headers, all merge cases, SIG member fallback).

- **Built an in-house positive control for the identity disclosure and validated the
  pipeline** (2026-08-28). Extended `emulate.py` with `--positive-control`: the dongle
  advertises a Resolvable Private Address and accepts an unauthenticated Just Works bond,
  distributing its IRK + identity. blatann 0.6.0 supports both
  (`BleDevice.set_privacy_settings(enabled, resolvable_address=True)` and
  `security.set_security_params(bond=True, io_capabilities=NONE)`; `_get_security_params`
  always sets `kdist_own.id_key=True`, so the identity is distributed on every bond). The
  two BLE radios are independent, so the dongle is the peripheral and the host adapter the
  central in one machine — no phone needed. `identity.extract` against the emulated RPA
  gave `Bonded: yes` → `IDENTITY ADDRESS DISCLOSED — CE:C1:1B:51:DD:23`, correctly
  attributed. This proves `identity.extract` and the disclosure pipeline are correct in
  this environment, so the 2026-08-28 Redmi Buds 6 Play negative is a device property (it
  refuses the bond), not a tooling/stack defect. Generalises the T0.2 "emulate the target
  to test the observer" method from *observation* to *disclosure*. `emulate.py` change is
  in the working tree (not yet committed).

- **Configured the `mattpocock-skills` engineering skills for this repo** (2026-08-02),
  via their `setup-matt-pocock-skills` scaffolder. Three config files under
  `docs/agents/` plus an `## Agent skills` section in `CLAUDE.md` that points at them.
  Choices made, with the reasoning: **issue tracker = local markdown** under
  `.scratch/<feature>/` rather than GitHub Issues — the project is single-maintainer and
  the repo already keeps its planning in-tree (`NOTES.md`), so issues stay next to the
  code and get versioned with it, and `.scratch/` was deliberately left **out** of
  `.gitignore` for that reason. **Triage labels = the five canonical roles verbatim** —
  no pre-existing label vocabulary to map onto. **Domain docs = single-context** —
  one package, no monorepo signals; `CONTEXT.md` and `docs/adr/` are not created now,
  `/domain-modeling` creates them lazily when a term or decision actually needs pinning.
  Nothing here changes runtime code or the scan/select/act model; it only tells those
  skills where to read and write. Note the overlap to watch: `NOTES.md` stays the
  project's working memory (rationale, TODO, findings) — `.scratch/` is for per-feature
  specs and tickets, not a second scratchpad.

- **Realigned the recon stage with `/opt/repo/Whisper_Bully`** (2026-07-31). The goal for
  phase 1 of this project is to reproduce every recon / enumeration / fingerprinting
  capability, so the identity-address stage is reproduced here rather than delegated.
  What the comparison changed is recorded in *Resume here* and in the finding *"The
  target is induced, not found"*; the short version is that our filter looked for the
  wrong thing and our check sampled the leak one step too late. Worth keeping in mind as
  a method: the reference tool is 726 lines and its README states the mechanism
  explicitly — reading it first would have saved four sessions of live guessing.
  Deliberately **not** copied: Whisper_Bully's stages 2–3 (L2CAP flood, SMP bond during
  stack recovery) stay out, per the passive/active boundary in CLAUDE.md.

- **Automated the target triage** (`scan_fused.py`), after live use showed the workflow
  put the decisive information (`Conn`) on one table and the action on the other.
  Activity 3 now runs **both radios at once** and merges by address; the per-device
  confirmation prompt was removed (the menu entry *is* the explicit action, and the
  private address rotates while you answer). Additions:
  - `scan_fused.is_viable()` — the target profile in one place: `rnd-r` **and**
    connectable. The menu labels the action VIABLE TARGET or "not viable, will explain".
  - `identity.extract(connectable=False)` now refuses **without transmitting**, like the
    existing `pub`/`rnd-s` refusals. Three passive refusals now cover every dead end we
    actually hit live, so a wasted minute is no longer possible.
  - `livetable` gained an optional filter hook (`toggle_filter`/`filter_hint`, key `V`)
    used by the fused scanner for viable-only rows. Other scanners are unaffected.
  - Liveness probe in `identity.extract`: the 8 s discovery window before connecting
    records whether the target is still on the air *under that address*. This is what
    separates "address rotated" from "advertises non-connectable" — previously the two
    produced an identical silent failure and cost a minute each to guess at.
  - Validated: fusion/viability/filter/CSV and the three passive refusals offline with
    synthetic records from both backends; BlueZ half + degraded mode live; full CLI
    walkthrough via pty. **The fused `Conn` column is now validated against real
    hardware too** (2026-07-31, after the USB fault was cleared by a reboot): real
    `YES`/`no` values, `?` only where the dongle genuinely missed the device, and the
    viable-only filter correctly emptying the table in an environment that contains no
    viable target. The action the table feeds (`identity.extract`) remains unvalidated
    against a real RPA — that is the last gap before this can be a checkpoint.

- Added **BLE scan over the host adapter** (`scan_le.py`) + the **identity address
  (BDADDR) disclosure check** (`identity.py`), reachable as activity 3 → device menu.
  Rationale in *Design decisions* above. Implementation notes worth keeping:
  - `ClassicScanner` gained a `transport` argument and three subclass seams
    (`record_cls`, `_needs_info`, `_set_prop`, plus `columns`/`csv_prefix`/`_extra_csv_*`)
    so the LE scanner is ~130 lines instead of a second scan loop. Classic behaviour
    unchanged (re-verified: CoD/Icon/RSSI decoding still correct).
  - BlueZ **5.85 emits `ManufacturerData.Key` / `ServiceData.Key` with a dot**, not a
    space — the first live run silently produced an empty Manufacturer column. Both
    spellings are accepted now.
  - The address *kind* (`rnd-s`/`rnd-r`/`rnd-n`) is not reported by BlueZ; it is derived
    from the top two bits of the MSB octet in `exposure.addr_kind` and shared with the
    dongle vocabulary. Verified live against 16 neighbouring devices.
  - `bluetoothctl` registers a pairing agent at startup and BlueZ allows a single
    *default* agent, so the scanner's session would block the check's NoInputNoOutput
    agent. Hence `ClassicScanner.release_agent()/restore_agent()`, called by `cli.py`
    around the action; the report flags it if the default-agent request still failed.
  - `identity.extract` runs its own `scan le`: BlueZ evicts non-bonded devices from the
    cache seconds after discovery stops (`[DEL]` events), and the caller has just paused
    its scanner.
  - Validated: offline parsing/reporting; live LE scan (16 devices, address kinds and
    vendors correct); full CLI walkthrough via pty; the active path end-to-end against a
    **non-existent** address (session, snapshots, ambient filter, cleanup, all four
    report shapes). Not yet run against a real RPA target — see TODO.

- Fixed system-python invocation for the RFCOMM/D-Bus helpers: `subprocess.run(["python3", ...])`
  resolved to the **venv** python (no AF_BLUETOOTH / dbus) when the CLI runs inside the
  venv → AttributeError. Now `spp.py`/`pbap.py` call `/usr/bin/python3` explicitly
  (`_system_python()`). Also: SPP console now runs `bluetoothctl connect` first (RFCOMM to
  an idle paired device fails EBADE) and flags `br-connection-key-missing` (device dropped
  the bond — re-pair). Headsets drop our bond when they reconnect to a phone.

- Restructured the **Classic device menu** for UI consistency with BLE: SDP profiles
  are now **interactive nodes** (select *Serial Port (SPP)* → open console; *Phonebook
  (PBAP)* → probe/sample; others → description), and added a **Pair / bond** action
  (`scan_classic.pair`, box-initiated) since active actions need a bond and inbound
  pairing fails in the VM. Helpers `capabilities.classic_detected/classic_note`.
  Validated live on the Jabra (Profiles → SPP → console).

- Added Classic **SPP serial console** (`spp.py` + `spp_console.py`): finds the Serial
  Port (0x1101) RFCOMM channel via `sdptool`, opens an interactive RFCOMM send/receive
  console. **Validated live** against a paired Jabra Evolve2 65 (channel 3). RFCOMM
  socket runs in the **system python3** — the venv interpreter is built WITHOUT
  AF_BLUETOOTH (checked). Needs a paired device.

- Added Classic **PBAP** (`pbap.py`) under the Classic device menu: *access probe*
  (obexctl OBEX session → accepted/rejected, no contacts) + bounded *sample pull*.
  **Validated live** against a paired OnePlus Nord. Needs a **paired** device; never
  pairs/bypasses bonding. Key findings: obexctl `pull` can't fetch a single vCard
  (only the whole book), so the bounded sample uses BlueZ D-Bus
  `PhonebookAccess1.PullAll` with the **`MaxCount`** filter (not `MaxListCount`) — a
  true protocol-level bound. That D-Bus call runs in the **system python3**
  (dbus-python isn't in the venv) via `pbap_pull.py`. Menu key `b` = Back, so PBAP
  uses `a`. Pairing a phone in this VM only worked **box-initiated** with
  `agent KeyboardDisplay` + numeric-comparison confirm; inbound (phone→box) never
  reached the stack.


- Initial bench built: BLE scan (batch + live), Classic scan, GATT client browser,
  peripheral emulation, interactive scan→select→act workflow, Fast Pair identification,
  passive Fast Pair GATT exposure check. (Details in git history on `devel`.)
- Added BLE **capability fingerprint** (`capabilities.py`): connects, enumerates GATT,
  matches a signature registry (DFU/OTA, HID, NUS, OTS, DIS, Mesh, IPSP, LE Audio,
  Media/Telephony) + attack-surface notes. Detection-only. Registry is extensible.
- Added Classic **profile capability enumeration** (`capabilities.classic_report` +
  `scan_classic.read_profiles`): parses SDP `UUID:` lines from `bluetoothctl info`
  into a profile checklist (A2DP/AVRCP/HFP/SPP/OBEX/PBAP/MAP/HID/PAN/SAP/DID).
  Detection-only. Parser validated against real soundbar output.
- Reworked per-device interaction into a **navigable menu tree** (`menu.py`): stack of
  `Menu` nodes, universal Back, breadcrumb, `on_enter`/`on_leave` (GATT node connects/
  disconnects). GATT read/write/subscribe are now leaf nodes; replaced the bespoke
  `interactive_gatt`/`_device_action`. Live-validated (read of Device Name via tree).
- Turned the BLE **Capabilities** action into navigable nodes: each detected capability
  splits **Inspect (read-only)** vs **Interact (active)** into separate sub-menus.
  Inspect → `client.read_all` (Device Information → read-all); Interact → write/subscribe
  + Nordic UART `client.serial_console`. Removed the old `capability_fingerprint` leaf.
  Registry helpers `capabilities.signatures/match_services`.
- Firmware capability → Inspect → **DFU exposure verification** (`capabilities.dfu_exposure`):
  passive; reports OTA variant, buttonless (remote-trigger) char, writable control point
  on the unauthenticated link, with a verdict. Does NOT trigger/upload (brick risk stays
  out; the generic Interact→Write can send a trigger at the tester's discretion).
