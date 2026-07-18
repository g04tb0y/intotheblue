# NOTES

Working memory for this project. See `CLAUDE.md` for how it's used. Git holds the
detailed change history; this file holds rationale, plans, and open questions.

## TODO

- [ ] Push pending local commits to `origin/devel` (branch is a few commits ahead).
- [ ] Optional: make the `csv/` export order match the on-screen discovery order
      (currently CSV is sorted by RSSI).

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
- **Passive/active boundary** — passive analysis (`exposure.py`, record enrichment)
  must not transmit; anything that connects/writes sits behind an explicit user action.
  Heavy active primitives (e.g. L2CAP flooding) live in external, dedicated tooling.
- **English for code and all UI text.**

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
