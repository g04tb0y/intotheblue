# NOTES

Working memory for this project. See `CLAUDE.md` for how it's used. Git holds the
detailed change history; this file holds rationale, plans, and open questions.

## Current state — 2026-11-04

**Last work: the Sniffle backend (3rd scan radio) through Phase 3.** The CC2652P/Sonoff
flashed with Sniffle (v1.11.0, 2 Mbps) is now a full scan→select→act backend alongside
nRF52/blatann and BlueZ. Advertising scan (Phase 1), connection following (Phase 2), PCAP
export + a persisted `connection` table + engine/web/MCP wiring (Phase 3) are all done and
hardware-validated via the positive-control pattern (`emulate.py --name TestDevice` as the
peripheral + nRF Connect on a phone forcing the connect). Spec/tickets in
`.scratch/sniffle-scanner/`. Committed through `b0943ac`.

**Immediate next step:** rework the follow UI into **click-to-follow** — selecting a device
from the scan table triggers the follow action, no copy/paste of the address (today's MAC
input in the web controls is a stopgap). This is the scan→select→act model the whole bench
is built on. Also re-confirm the PCAP export click-through on real hardware.

**Also open:** Fast Pair **Key-based Pairing handshake (item 2)** is plumbed end-to-end but
never got a clean positive result — real Redmi buds return **0 notify** even with the right
discoverable frame and official anti-spoofing key. Closing it needs the emulated Provider
(`emulate.py --fast-pair-gatt`) on a **second nRF52** (PCA10059). See TODO + Findings.

**Hardware in use:** nRF52 Connectivity dongle (serial, blatann) on `/dev/ttyACM*` (by-id);
host BlueZ adapter `hci0`; Sonoff ZBDongle-P / Sniffle on `/dev/ttyUSB0`. Full inventory in
`docs/HARDWARE.md`.

**Validated checkpoints:** `2fbe3c8` — the webui iteration (single-page tab strip, Devices
recency/session filters + prune, dongle-side Pair), maintainer-confirmed on hardware. The
Sniffle backend is committed through `b0943ac` (follow / persistence / `/connections`
validated via the webui; the PCAP export click-through is the one bit still to re-confirm,
see TODO). The disclosure recon half (`72b680b`) is a save-point, not a "disclosure works"
checkpoint (see Findings).

## TODO (open only)

**Sniffle**
- [ ] Rework follow into **click-to-follow** (select from the scan table → follow action),
      replacing the stopgap MAC input in the web controls — match scan→select→act.
- [ ] Re-confirm the **PCAP export** click-through on hardware (the per-row button was moved
      into the controls after it didn't work from the htmx-swapped table row).

**Fast Pair**
- [ ] **Handshake item 2 — close it with the positive control.** `emulate.py
      --fast-pair-gatt` (needs the **2nd nRF52 / PCA10059**): a known-good Provider proves the
      Seeker end-to-end without depending on the Redmi behaviour. In parallel, retry against
      buds in pairing mode (buds in case, lid open, **hold the case button until the LED blinks
      rapidly**) connecting the **fresh discoverable RPA** (rotates each pairing). The Redmi
      `FB9B72` anti-spoofing public key is already cached in `fastpair_keys.yaml` (from Google
      metadata; the GMS endpoint needs `X-Android-Package`/`X-Android-Cert` headers + an
      operator API key via `$INTOTHEBLUE_FASTPAIR_API_KEY` — neither committed).
- [ ] **Item 4 — complete pairing flow**: ECDH handshake → Passkey (`…1235`) → Account Key
      write (`…1236`). Completing the pairing is how we obtain a persistent reconnect key;
      the only prerequisite is the Provider's anti-spoofing **public** key (resolvable via
      `fastpair_keys.py`, offline file or opt-in Google lookup).

**GATT / webui / CLI**
- [ ] **GATT Device Name backfill** — plumbing proven (`store.set_name` + 0x2A00 capture);
      still need one clean dongle discovery on an intermittent advertiser to confirm the
      live path end-to-end.
- [ ] **CLI dongle-side Pair parity** — `gatt_pair(dongle)` lives in webui+MCP but not the
      CLI GATT menu (CLI's only Pair is Classic/BlueZ via `scan_classic.pair`). Decide
      whether to add it to the CLI or accept the CLI as the thinner front-end.
- [ ] **MCP ⇄ webui parity (ongoing)** — 25 tools today, in parity. As new tools land (full
      Fast Pair pairing, multi-radio), add the MCP wrapper in the same change.

**Classic**
- [ ] Re-validate the **SPP serial console** end-to-end once the Jabra bond is restored (they
      drop the box's bond when reconnecting to a phone); last full CLI run stopped at
      `br-connection-key-missing`.

## Design decisions

- **Web UI theme = CSS token rewrite, not a rebuild.** The "Arasaka/cyberpunk" look
  (`webui/static/style.css`) is a *values* rewrite of the existing `:root` token palette plus
  additive rules — token/class NAMES are unchanged, so every component inherits the new look
  with **zero** markup/HTMX/`tabs.js` changes. Deliberately balanced (it's a data-dense tool):
  palette + chamfered (`clip-path`) edges + light HUD framing, **no** animated scanlines/neon.
  The palette is anchored to the passive/active boundary — cyan/teal (`--accent`/`--scan`) =
  passive/safe, red (`--err`) = active/transmitting — so ACTIVE vs PASSIVE badges read at a
  glance (see the Logs page). Chamfer technique notes live in the CSS header comment: clip-path
  clips borders too (the notched corner *is* the look on small controls) and clips outset
  box-shadow, so panel glow uses `filter: drop-shadow()` and left accent bars use inset shadow.
- **The MCP/webui server is the agent's end-to-end test surface — use it, don't stop at
  "headless".** When a server is up, drive it (JSON API `http://127.0.0.1:<port>/api/...` or
  MCP at `/mcp`) against the real dongle. Bounds that are real (state them, don't use as an
  excuse): the server owns the **one** dongle (one process; restart to load new code — uvicorn
  runs without `--reload`; don't kill the maintainer's live session without asking), and
  genuinely physical steps (device in pairing mode, a 2nd nRF52, operator secrets like
  `$INTOTHEBLUE_FASTPAIR_API_KEY`) are the maintainer's. See [[use-mcp-webui-server-to-test]].
- **Offline posture is a default, not a ban.** OTel off and the passive Model-ID→name lookup
  (local YAML over Google) are defaults; a network call a test genuinely needs (e.g. fetch an
  anti-spoofing public key from Google by Model ID) is in-scope, opt-in, and logged
  `boundary="network"`. See [[offline-posture-not-a-test-blocker]].
- **Python 3.10 pinned** (`.venv310/`) — `pc-ble-driver-py` (native, under blatann) ships no
  wheels for ≥ 3.11. Non-negotiable until that changes upstream.
- **Three independent hardware backends, kept separate** — nRF52 (serial/blatann) for BLE;
  host BlueZ (`bluetoothctl`) for Classic; Sonoff/Sniffle (serial, no blatann) as a passive
  receiver. Separate serial devices, so they never contend. Do not conflate.
- **Dongle stays open across an interaction** — pause the scan and connect on the **same**
  `BleDevice`. Close-then-reopen immediately fails with `rpc_h5_transport_state`; this drove
  the whole scan→pause→act→resume design.
- **Table keeps discovery order** (no live RSSI re-sort) so rows don't jump under the cursor
  during selection.
- **Shared `livetable` scanner interface** — new scan surfaces implement
  `snapshot/is_scanning/pause/resume/save_csv` instead of duplicating the curses UI.
- **Sniffle scan→select→follow (not blind follow)** — following a peer **without a target** is
  unreliable (parked on one channel → catches `CONNECT_IND` by luck) and bonded devices rotate
  their RPA, so a hand-typed MAC is useless. Instead `reconfigure(follow=True, targ_mac=…)`
  retargets the **same open dongle** onto the address seen **at selection time** — "I follow it
  as I see it in the scan". Sniffle is **receive-only** in follow mode (the only TX path is
  `--active` SCAN_REQ in Phase 1).
- **Fusing the two BLE radios** (`scan_fused.py`) — they hear the same advertising at the same
  instant, so they report the same address (even a rotating RPA) → mergeable by address. Needed
  because connectability (the advertising PDU type) is known **only to the dongle** while the
  host-side action runs **only through BlueZ**; triaging on one table and acting from the other
  let the address rotate in between. Dongle stays optional (degrades to BlueZ-only, `Conn=?`).
- **Two target signals, never collapsed into one boolean** (`scan_fused.target_note`) — `FP`
  (advertises Fast Pair = in pairing mode, attributable by name) and `RPA` (`rnd-r` +
  connectable = something to unmask) answer different questions. `is_viable()` keeps its old
  meaning and is one input of the two.
- **Prefer connect-only over pairing** (`identity.extract`) — attempt the bond only when the
  connection produced nothing attributable; the least intrusive path that still answers.
- **BLE over BlueZ** (`scan_le.py`) — deliberate exception to "backends separate": host-side
  actions only work on devices in BlueZ's own cache, so the feeding scan must run on `hci0`
  too. Implemented as a subclass of `ClassicScanner`, not a fourth scan loop.
- **`connectable` is `None`, not `False`, on BlueZ** — BlueZ doesn't surface the advertising
  PDU type; `exposure.assess` reports "unknown" rather than guessing.
- **Identity disclosure is attributed, never inferred** (`identity.py`) — a new address counts
  only if it carries the target's name or a `Paired:`/`Bonded: yes` line, and cleanup only
  touches entries this run introduced (removing a bonded neighbour would destroy a real bond).
- **Held-open GATT interaction session** — keep ONE peer connected across requests
  (`gatt_connect`/read/write/subscribe/disconnect) instead of connect-per-op, so the scan is
  paused once per session. Notifications buffer per characteristic (append under the GIL, poller
  swaps the list); an **idle watchdog** (`GATT_SESSION_IDLE`) auto-closes a session left open so
  the scan is never silently pinned paused.
- **Pair over two distinct radios** (`gatt_pair`) — `method="dongle"` (central-side Just Works
  on the held session peer via blatann) and `method="bluez"` (`scan_classic.pair` over hci0) run
  on physically distinct radios → no contention. A refused bond is a valid result, not an error.
  The `method` param is shaped to grow a chosen-radio option (future multi-radio).
- **Fast Pair crypto isolated in `fastpair.py`** (top-level, no blatann) — ECDH secp256r1 →
  SHA-256[:16] → AES-128-ECB; shared by the Seeker (`engine`) and the emulated Provider
  (`emulate.py`), unit-testable headless.
- **Single-page tab strip with per-device iframes** (`base.html` + `tabs.js`) — a device opens
  as an in-page tab whose body is an `<iframe>` of `/devices/{addr}?bare=1`. The iframe is
  deliberate: the detail page has fixed `id`/`hx-target`s and holds a GATT session, so embedding
  several fragments in one document would collide; the iframe isolates ids/styles/session.
- **Passive/active boundary** — passive analysis (`exposure.py`, record enrichment) must not
  transmit; anything that connects/writes sits behind an explicit user action. Heavy active
  primitives (e.g. L2CAP flooding) live in external, dedicated tooling.
- **English for code and all UI text** (chat with the maintainer is in Italian). See
  [[language-code-english]].

## Findings

- **Identity disclosure does not reproduce on a real Fast Pair target, and the reference tool
  fails the same way** (2026-08-28, Redmi Buds 6 Play; full run in
  `docs/test-plan-redmi-buds-6.md`). The host opens an unauthenticated LE connection and reads
  full GATT, but the Just Works bond is refused (`AuthenticationCanceled`) and no identity
  address enters the BlueZ table — virgin *and* phone-bonded. `Whisper_Bully` fails identically.
  Root cause: resolving an RPA needs the peer's IRK, distributed only on a **completed** bond; a
  host that never bonded the peer cannot resolve the RPA at connection — so WB's README claim of
  resolution at `LL_CONNECTION_COMPLETE` (before SMP) is empirically wrong here. **Proven
  correct in this environment** by an in-house positive control (`emulate.py
  --positive-control`): disclosed `CE:C1:1B:51:DD:23` end-to-end → the Redmi negative is a
  device property, not a tooling bug. See [[identity-disclosure-target-is-phone-bonded]].
- **The target is induced, not found — and the leak is at the connection, not the pairing**
  (2026-07-31, from reading `/opt/repo/Whisper_Bully`). A device emits discoverable Fast Pair
  advertising **only while a human holds it in pairing mode** — which is also what makes it
  attributable. The `rnd-r + connectable` filter describes where the mechanism works but as a
  search strategy selects strangers' phones/watches. Scanning the ambient environment for a
  target is structurally futile; the target is induced. (WB filters on FE2C, never on address
  kind.)
- **Phones are not a target for the identity check** — in "pair new device" a phone is
  discoverable over **BR/EDR**; its LE advertising is Google Nearby (`FEF3`) and non-connectable
  → no LE connection → no SMP → nothing to distribute. The target class is accessories
  advertising a **connectable** RPA that accept Just Works.
- **You cannot attribute an RPA to an owner before acting on it** — the address rotates, so the
  row viable now can't be matched to the device you identified a minute ago. Working procedure:
  bring the authorised device to a few cm and pick the row by **overwhelming RSSI** (≈ −40 vs
  neighbours at −88…−100); keep the scan short before acting (an RPA doesn't wait).
- **Phone-connected devices are invisible to passive scanning** — a device bonded to and
  connected with a phone communicates on hopping data channels and does **not** advertise
  (BLE) / is **not** in inquiry scan (Classic). All of intotheblue's identification is
  advertising/inquiry based, so the thing we most want to enumerate is exactly what scanning
  can't see. The answer is connection **sniffing** (Sniffle for BLE — now integrated; Ubertooth
  for Classic — not owned). See [[goal-identify-paired-bt-devices]].
- **Sniffle cannot decrypt a bonded reconnection** — it is LESC-encrypted (ECDH) even if you
  capture the pairing. Following gives the **relationship** (phone ↔ device), addresses
  (possibly RPA), timing and packet structure — not the GATT payload. For "who is paired with
  whom" that is still the key win. Audio accessories (Redmi) reconnect over **BR/EDR Classic**,
  invisible to a BLE sniffer; their BLE link appears only during the first Fast Pair handshake.
- **The Redmi Buds 6 Play never populate a Fast Pair account key filter** (`0000` across three
  addresses and a full forget + re-pair via the half-sheet). A plain Bluetooth bond does **not**
  write a Fast Pair account key — a non-empty filter needs the device saved to a Google account.
  So the non-empty/"bonded" branch of `bond.py` is validated on synthetic frames only.
- **Fast Pair handshake item 2 → 0 notify even with the right frame and key** (2026-11-04). The
  Seeker crypto is proven correct headless (full request→response round-trip). Against live
  Redmi buds: connected the **discoverable RPA**, wrote a valid 80B Case-1 KBP to `…1234` with
  the official anti-spoofing key (`FB9B72`, from Google metadata) → still 0 notify (25-30 s),
  stress idem, link never dropped. Reading: on anti-spoofing verification failure the Provider
  **stays silent** (spec). Residual unknowns: possibly the wrong `provider_addr` encapsulated
  (public vs RPA), or a hardened firmware ignoring KBP Case-1 from a non-Android central. The
  definitive test is the emulated Provider (positive control). BlueZ sees/subscribes FE2C but
  `gatt.write` → `NotSupported` on every type; **the dongle is the right radio for the write**,
  BlueZ only to *see*.
- **VMware USB passthrough is a real failure mode** — `NrfError.timeout` from `open_device`,
  `EPROTO` on `/dev/ttyACM0`, continuous `xhci_hcd … not part of TD` in `dmesg`. The dongle and
  the host BT adapter share one virtual hub, so one USB fault takes out **both** BLE backends;
  recovery needed a reboot of host and guest. Check `dmesg | grep xhci` first. Distinct from the
  transient `rpc_h5_transport_state` between back-to-back sessions, which idle time cures.
- **`open_device`'s by-id autodetect earns its keep** — mid-session the dongle re-enumerated
  from `ttyACM0` to `ttyACM1` after an xhci fault and everything kept working because the lookup
  goes through `/dev/serial/by-id/*nRF52*`. Never hardcode the node.
- **The dongle stalls after many connect/disconnect cycles** — after a heavy GATT-session run
  the `ble` scan may see only a few rows and new connects start failing. A **restart of the scan
  (fused)** reopens the dongle and recovers it. Same family as the transient
  `rpc_h5_transport_state` that idle time cures; consider auto-recovery.
- **Emulate the target to test the observer** — the fastest way to prove a scan-side decoding
  path is to make the dongle transmit the advertisement and read it back on the BlueZ side (the
  radios are independent). Settles in minutes what ambient scanning cannot. Limit: it validates
  **observation**, never **disclosure** (no secret to leak). Constraint: the dongle **cannot
  advertise and scan at once** — test the scan side with `scan_le.LeScanner` alone, not the
  fused scanner (which resets the busy dongle and kills the beacon).

## Hardware

Full inventory (confirmed vs inferred, with sources) in `docs/HARDWARE.md`. Live-relevant:

- **nRF52 Connectivity dongle** — BLE via blatann/`pc-ble-driver-py`, serial on `/dev/ttyACM*`
  (autodetected by-id). Never appears in `hciconfig`.
- **Host BlueZ adapter `hci0`** — Classic (`bluetoothctl`) + the BlueZ-side BLE/GATT/Pair paths.
  Needs `sudo systemctl start bluetooth && sudo hciconfig hci0 up`.
- **Sonoff ZBDongle-P (CC2652P)** — flashed Sniffle **v1.11.0 full / CP2102N (2 Mbps)**,
  CRC-verified, runtime-verified decoding clean at 2 Mbps. `/dev/ttyUSB0` (by-id
  `…ITead_Sonoff…`). Flashing helper + procedure in `.scratch/sonoff-sniffle-flash/`.
- **2nd nRF52 for the Fast Pair positive control — decided: PCA10059** (Nordic nRF52840
  Dongle, ~€10-12): the exact board `pc-ble-driver-py` ships precompiled Connectivity firmware
  for (USB-CDC), flashable over USB (serial bootloader, no SEGGER). Match the `sd_api` version
  of the existing dongle. Avoid the Seeed XIAO BLE (no precompiled connectivity hex).
- **DSD TECH SH-U09C5 (FTDI FT232RNL, 3 Mbps)** — 3.3 V USB-UART with DTR/RTS break-out for
  auto-BSL; kept for re-flashing CC2652P boards.

## Goals (standing)

- **Identify as many paired Bluetooth devices as possible.** The negative identity-disclosure
  result retires neither the goal nor the direction — pursue it by whatever fits scan/select/act
  (advertising, GATT, Classic SDP/CoD, proximity, connection sniffing). See
  [[goal-identify-paired-bt-devices]].

## Open questions — to study / evaluate

- **Multi-radio orchestration.** The bench has three heterogeneous radios (hci0/BlueZ, nRF52,
  Sonoff/Sniffle), but `engine` assumes **one** nRF52 (single autodetect, one open `BleDevice`,
  CLI⇄web exclusive). A real model: select an nRF52 by `/dev/serial/by-id` (to run more than
  one), a scan-radio vs act-radio policy (Sniffle sniffs while the Nordic connects, removing the
  scan pause), and Sniffle as a first-class `livetable` scanner. `gatt_pair`'s `method` is
  already shaped for a chosen-radio option.
- **Classic (BR/EDR) sniffing** for devices already connected to a phone over an ACL link —
  needs **Ubertooth One** (or similar) to recover the piconet and follow the hop; the nRF52
  can't do BR/EDR at all. Blocked on hardware we don't have (the JBL Go 3 ↔ iPhone case).
- **Class of Device minor** decoding covers only common majors (`scan_classic._MINOR`); extend
  as needed.
- **Roadmap depth**: active enumeration, richer peripheral/server interaction, and device
  cloning/emulation — still to be designed into the scan/select/act model. First cloning step
  done: `emulate.py --fast-pair MODELID` clones a discoverable FP advertisement; `--fast-pair-gatt`
  stands up a real FE2C GATT Provider. Next: clone the Model ID straight from a live capture.

## Actions log (recent first)

Rationale beyond what git shows; not a complete changelog (git is).

- **Web UI → true single-page app (SPA shell)** (2026-11-11). The maintainer wanted the three
  top-level views to behave as one page: returning to Scan must **not** close the opened device
  tabs. Reworked from the old model (the "home" tab *was* the page, nav links navigated away,
  closing all device tabs) into a shell (`app.html`) with **three fixed iframe tabs**
  (Scan/Devices/Logs → `/panes/scans|devices|logs`) + on-demand closable device/connection
  tabs. Extended the already-committed device-detail iframe-isolation pattern to the three
  panes; switching tabs is show/hide (no reload) so tabs persist and the scan keeps polling in
  its hidden iframe. `tabs.js` now runs in both the shell and every bare iframe (branch on
  `#tabbar` presence) and routes a pane row-click up to the shell via `window.top`. New routes:
  `/` `/devices` `/logs` `/connections` all return the shell; `/panes/*` serve the bare bodies;
  pane forms post to `/panes/*` and prune redirects to `/panes/devices` so the iframe never
  nests the shell. Validated via headless Chromium (shell + panes render, routes 200). Left
  uncommitted, server left running on :8000 for maintainer click-through.
- **Web UI "Arasaka" cyberpunk restyle** (2026-11-11). Retheme of `webui/static/style.css`
  from the reference images in `UI_reference_style_only/` (staged by `d630c57`). Scope chosen
  with the maintainer: **web UI only** (curses CLI left as-is), **balanced** intensity
  (readability first — no scanlines/neon), **no new font** (system stack + mono for hex). Done
  as a token-value rewrite + additive rules (chamfered `clip-path` edges, cyan/amber/red
  semantic palette mapped to the passive/active boundary, mono HUD headers, left accent bars,
  corner HUD code). No markup/HTMX/`tabs.js` touched. Validated with headless Chromium
  screenshots of `/`, `/devices/{addr}`, `/logs` against the live server (438 persisted
  devices) — all render, dense tables stay legible, ACTIVE(red)/PASSIVE(teal) badges clear.
  Not yet maintainer-validated in a real browser; left in the working tree (uncommitted).
- **Sniffle backend, Phases 1-3** (2026-11-04). Third scan radio, independent of blatann.
  Phase 1: `sniff.py` wraps `SniffleHW` as a `livetable` scanner (local LTV parser since
  Sniffle hands raw `adv_data`, blatann-free copies of the vendor/Fast-Pair tables; default
  PASSIVE_SCAN = 0 TX, `--active` sends SCAN_REQ). Phase 2: connection following
  (`SnifferMode.CONN_FOLLOW`, `ConnectionRecord` keyed by access address) — honest device name
  from the device's own advertising (the link is ciphertext; the `encrypted` flag records that).
  Phase 3: PCAP export (`sniffle.pcap`), a persisted `connection` table in `store`, and engine +
  web + MCP wiring (follow/stop-follow/export, `/connections` page). HW-validated via
  positive-control (`emulate.py --name TestDevice` + nRF Connect). The follow UI is a stopgap
  MAC input — click-to-follow is the next step. Committed through `b0943ac`.
- **Fast Pair GATT — passive profile + active handshake/stress + pubkey fetch** (2026-11-03).
  New `fastpair.py` (crypto/codec, no blatann). Item 1 (passive): `engine.fast_pair_profile`
  over the stored FE2C tree + Fast Pair tab + MCP tool. Item 2 (active): `fastpair_handshake`
  on the held session (encrypted KBP write → awaited notify → decrypt; non-decrypt = finding).
  Item 3 (active): `fastpair_stress` malformed writes. `emulate.py --fast-pair-gatt` is the
  in-house Provider. `fastpair_keys.py` resolves the anti-spoofing **public** key by Model ID
  (local YAML first, then opt-in Google `nearbydevices-pa` lookup, cached). Items 1+3 work vs
  real Redmi with one dongle; item 2 gets 0 notify (see Findings) → needs the emulated Provider
  (2nd nRF52). Item 4 (full pairing) deferred.
- **webui Pair action** (2026-11-03). `engine.gatt_pair(address, method)` — `dongle`
  (central-side Just Works on the open session peer) and `bluez` (`scan_classic.pair` over
  hci0), chosen in the UI. A refused bond is reported faithfully. MCP tool `gatt_pair`.
- **webui single-page tab strip** (2026-11-03). Replaced the `window.open('_blank')` nav with an
  in-page `#tabbar`: a fixed "home" tab (Scans/Devices/Logs, keeps polling while hidden) + a tab
  per open device, each an `<iframe>` of `/devices/{addr}?bare=1`. Several devices open at once;
  the iframe isolates ids/styles/GATT session.
- **Devices list organization** (2026-11-03). `store.list_devices` gained two non-destructive
  view filters (`since` recency window, `session_id` current/last-scan lens) and
  `prune_devices(before)` is the one destructive op. Devices page: "Seen within" dropdown
  (default 24h) + "Current/last scan only" + a confirmed prune form. MCP in parity.
- **Structured event logging + Logs page** (2026-11-03). `eventlog.py` (stdlib, no blatann):
  JSONL sink that `engine` writes from one place via an `_op_log` context manager wrapping each
  radio op (`outcome` ok/error/start, `boundary` passive/active; passive DB reads not logged).
  `/logs` page (filters + JSONL export) + always-on footer tail + MCP `query_events`.
- **webui GATT interaction + enrichment** (2026-11-02). Held-open session (`engine.gatt_*`),
  Capabilities view over the stored tree (passive), BlueZ value pass (`gatt_bluez._read_values`),
  Classic SDP detail, tabbed device detail. Capabilities re-fetches on `HX-Trigger: gatt-updated`
  so a live browse never leaves it stale.
- **GATT browse hardened on real hardware** (2026-11-02, Epson ET-3950). `webui/blatann_patch.py`
  finalises characteristic discovery on the SoftDevice `invalid_state` wedge (contained
  monkeypatch, never edits the installed package). `engine.browse_gatt` releases stale
  connections before connecting + always disconnects + retries a transport wedge + backfills the
  GATT Device Name. `gatt_bluez.py` enumerates vendor services over hci0 as a second path.
- **web UI + MCP + SQLite store** (2026-11-01). Third front-end over the same scanners:
  FastAPI (Jinja2 + HTMX, no build step) + JSON API + MCP (FastMCP at `/mcp`), backed by
  stdlib-sqlite `store.py` (no blatann → read side + tests load without hardware). `engine.py`
  is the single hardware owner (one scanner, one lock, background flush). OTel disabled for the
  offline posture.
- **Passive identification enrichment** (2026-08-28, hardware-validated). `bledev/fastpair.py` +
  `fastpair_models.yaml` (Model ID → product name, local registry); fixed fused Model-ID masking;
  `SvcNames` column; authoritative SIG dataset (`bledev/sig.py`); `oui.py` (public-MAC vendor via
  IEEE OUI); TX Power AD → dBm + path-loss estimate. All derived from **advertising**, so none
  can see a phone-connected device (see Findings).
- **Bond / pairing-relationship enumeration** (2026-08-28). Passive `bond.py` decodes the Fast
  Pair non-discoverable account key filter (existence/count, never identity — one-way Bloom
  filter, RPA peers), directed reconnect advertising, and host-side bond. Wired into
  `exposure.report`.
- **In-house positive control for the disclosure** (2026-08-28). `emulate.py
  --positive-control` makes the dongle advertise an RPA and accept a Just Works bond;
  `identity.extract` disclosed the identity end-to-end → the Redmi negative is a device property.
- **Classic stack** (earlier). Interactive Classic menu (Pair + navigable SDP → SPP console /
  PBAP), capability fingerprint (BLE `capabilities.py` + Classic `classic_report`), navigable
  menu tree (`menu.py`), DFU exposure verification (passive). RFCOMM/D-Bus helpers run in the
  **system** python3 (the venv lacks AF_BLUETOOTH / dbus).
- **Engineering skills configured** (2026-08-02). Issue tracker = local markdown under
  `.scratch/<feature>/` (single-maintainer, planning stays in-tree); triage = the five canonical
  roles; domain docs = single-context. See `docs/agents/`. `NOTES.md` stays the working memory;
  `.scratch/` is per-feature specs/tickets.
