# NOTES

Working memory for this project. See `CLAUDE.md` for how it's used. Git holds the
detailed change history; this file holds rationale, plans, and open questions.

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

**Caveat / next.** One device, no *known-vulnerable* positive control — so we can't
fully separate "device not vulnerable" from "this BlueZ version doesn't exhibit the
leak at all". The identical failure of both tools does rule out an intotheblue defect.
Getting an older/unpatched accessory that accepts a raw Just Works bond is the next
step to turn that caveat into a measured result.

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
- [ ] **Obtain a known-vulnerable Fast Pair accessory as a positive control** — one
      that accepts a raw Just Works bond — to confirm the disclosure path can work at all
      in this environment/BlueZ version, separating "device not vulnerable" from "this
      stack doesn't exhibit the leak". Without it the negative above has an untestable
      caveat (see *Resume here*, Caveat).
- [ ] **Fused Model-ID masking** (`scan_fused.snapshot`) — BlueZ's bare `fast_pair="Yes"`
      wins the `or` over the dongle's decoded Model ID, so the fused table and the passive
      exposure verdict understate a discoverable target (seen live 2026-08-28: buds in
      discoverable pairing mode reported "non-discoverable / medium"). Prefer the dongle's
      Model ID when present.
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

## Open questions — to study / evaluate

- **OUI vendor lookup** for public (non-random) addresses — would identify the vendor
  from the MAC even without manufacturer data; needs a local IEEE OUI database.
- **Service-UUID → SIG name** in the scan table (blatann has assigned-number tables;
  already used for GATT names in `client.py`, not yet surfaced in the scan columns).
- **TX Power** is not exposed directly by blatann's `AdvertisingData` in this version —
  would need to parse it out of the raw advertising records.
- **Class of Device minor** decoding covers only common majors (`scan_classic._MINOR`);
  extend as needed.
- **Roadmap depth**: active enumeration, richer peripheral/server interaction, and
  device cloning/emulation — all still to be designed into the scan/select/act model.

## Actions log (recent first)

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
