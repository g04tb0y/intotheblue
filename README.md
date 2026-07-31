# intotheblue — BLE test bench (nRF52 dongle + blatann)

A small bench to **develop and test Bluetooth LE devices** using a Nordic
**nRF52 dongle with Connectivity firmware** driven through `blatann`.

The dongle is a full BLE controller: it can act as **central** (scanner / GATT
client) and as **peripheral** (advertising / GATT server), so it covers both sides
of a test — something host-stack libraries (e.g. Bleak) cannot do.

## Hardware requirements

A Nordic nRF52 dongle/board flashed with the **Connectivity** firmware (serialized
SoftDevice). Once plugged in it shows up as `nRF52 Connectivity`
(VID `1915` / PID `c00a`) on a `/dev/ttyACMx` port.

## Environment setup

> **Important constraint:** you need **Python 3.10**. `pc-ble-driver-py` (the native
> Nordic library under blatann) does not publish wheels for Python ≥ 3.11, so with
> the system Python (3.13/3.14) installation fails.

```bash
# Create a Python 3.10 venv (uv downloads a standalone interpreter)
uv venv --python 3.10 .venv310

# Install the dependencies
.venv310/bin/python -m pip install -r requirements.txt
```

## Usage

The recommended entry point is the interactive CLI:

```bash
.venv310/bin/python cli.py
```

It shows a menu of activities; pick one, and when it finishes you're back at the
menu. All tools autodetect the dongle's port from `/dev/serial/by-id` (fallback
`/dev/ttyACM0`); you can force it with `--port`.

**Interactive workflow** — in any live scan you don't copy-paste MAC addresses:
move the highlight with ↑/↓ (or `j`/`k`), press **Enter** on a device, and it
becomes the target of a follow-up action. `Q` quits the scan without selecting.

Selecting a device drops you into a **navigable menu tree** (`menu.py`): each choice
either runs an action or descends into a sub-menu (e.g. Device → Browse GATT →
characteristic → Read/Write/Subscribe), `b`/Enter goes **back up** one level, and a
breadcrumb shows where you are. Depth is unlimited, so new capabilities just add
nodes. Nodes can hold resources across the descent — the GATT node connects on entry
and disconnects when you back out.

- **BLE** → choose an action on the selected device:
  - **Fast Pair GATT exposure check (passive)** — a read-only attack-surface
    classification from the advertising already collected (Fast Pair `FE2C` GATT
    service in/out of class, address-type identity/trackability, connectable,
    vendor/services). Sends no packets; it only helps prioritise which devices merit
    a scoped, authorised active test.
  - **Capabilities** — connect and enumerate the GATT database, then present the
    detected capabilities (firmware update / DFU-OTA, HID-over-GATT, Nordic UART
    serial channel, Object Transfer, Device Information, Mesh, IPSP, LE Audio,
    Media/Telephony…) as navigable nodes. Each capability separates **Inspect
    (read-only)** — e.g. Device Information → *Read all* dumps manufacturer/model/
    firmware/PnP — from **Interact (active)** — write/subscribe to its
    characteristics, or a Nordic UART serial console. Firmware update → Inspect →
    *DFU exposure verification* passively reports the OTA variant, whether a
    buttonless (remote-trigger) characteristic is present and whether the control
    point is writable on the unauthenticated link — without triggering anything.
    A *Show full report* option lists everything plus attack-surface notes.
    Signatures live in `capabilities.py`.
  - **Connect and browse GATT** — open an interactive GATT browser: services and
    characteristics are shown with their **SIG common name** (e.g. `2a00 Device Name`,
    `180f Battery Service`, from blatann's `UUID_DESCRIPTION_MAP`), grouped by service.
    Pick a characteristic by number to **read**, **write** (`hex:0a0b`, `text:hello`,
    or auto) or **subscribe** to notifications. Read values are **interpreted** when
    known — Device Name / Device Info as text, Battery Level as `%`, Appearance,
    Tx Power (dBm), PnP ID, Heart Rate (bpm) — otherwise shown as hex + ASCII.
- **Classic** device menu:
  - **Pair / bond** — box-initiated pairing (needed for active actions); inbound
    pairing is unreliable in a VM, so `scan_classic.pair` always initiates from here.
  - **Profiles & interactions (SDP)** — the device's SDP profiles (A2DP, AVRCP,
    HFP/HSP, SPP, OBEX, PBAP, MAP, HID, PAN, SAP, Device ID) as **navigable nodes**:
    - *Serial Port (SPP)* → **serial console**: finds the 0x1101 RFCOMM channel via
      SDP and opens an interactive send/receive console (`spp.py` → `spp_console.py`,
      run in the system python3 since the venv interpreter lacks AF_BLUETOOTH).
    - *Phonebook (PBAP)* → **access probe** (session accepted/rejected, no contacts)
      and a bounded **sample pull** (protocol-bounded via BlueZ D-Bus PullAll
      `MaxCount` — only N entries leave the phone; runs in system python3 via
      `pbap_pull.py`).
    - other profiles → a description.
  - **Show raw details** (`bluetoothctl info`). Signatures live in `capabilities.py`.
- **BLE via host adapter (BlueZ)** device menu — see *Live BLE scan via the host
  adapter* below:
  - **Identity address disclosure (active)** — tests whether a device advertising a
    Resolvable Private Address really keeps its permanent BDADDR private.
  - **Exposure check (passive)** — the same read-only classification as the BLE menu.
  - **Show raw details** (`bluetoothctl info`).

The dongle stays open across the interaction: scanning is paused while you act on a
device, then resumes — so after disconnecting you're back in the same scan.

### Live scan
Full-screen table that updates continuously as advertising packets arrive. Rows are
sorted by RSSI; the bottom bar shows the commands:

```
  ^P  pause/resume scan      ^S  save to CSV      Q  quit
```

Columns include several device identifiers beyond the name: **Addr** (address type:
`pub` = public, `rnd-*` = random), **Manufacturer** (from the Company Identifier),
and **Type** — a decoded protocol/product label from the manufacturer payload or the
service-data UUID (e.g. `Apple FindMy`, `MS SwiftPair`, `Xiaomi`, `Eddystone`). This
identifies many devices that advertise no manufacturer field. The decoding tables
live in `scan_live.py` (`_SERVICE_DATA_UUIDS`, `_APPLE_MSG_TYPES`) and are easy to
extend.

Also available standalone:

```bash
.venv310/bin/python scan_live.py
```

### Live Bluetooth Classic scan (BR/EDR)
Live table of **non-BLE** (Bluetooth Classic) devices — speakers, headsets, phones.
This does **not** use the nRF52 dongle: it drives the **host Bluetooth adapter** via
BlueZ, so you need a working `hci` controller:

```bash
sudo systemctl start bluetooth
sudo hciconfig hci0 up          # confirm with: hciconfig hci0  (UP RUNNING)
.venv310/bin/python scan_classic.py
```

The main identifier for a Classic device is its **Class of Device (CoD)**, decoded
into a readable label (e.g. `Audio/Video · Loudspeaker` for a JBL Go 3); the table
also shows Name, Icon and RSSI. Note: a Classic device only answers an inquiry while
it is **discoverable** (pairing mode) — one already connected or idle will not appear.

### Live BLE scan via the host adapter (BlueZ), and the fused scan
The LE twin of the Classic scan: same live table, same `hci0` prerequisites, but
`bluetoothctl scan le`. It does **not** replace the dongle scan — it exists because
host-side actions (below) only work on devices present in **BlueZ's own** device
cache, and a device seen by the dongle is invisible to BlueZ.

```bash
sudo systemctl start bluetooth
sudo hciconfig hci0 up
.venv310/bin/python scan_le.py       # BlueZ only
.venv310/bin/python scan_fused.py    # both radios at once (what the CLI uses)
```

**The fused scan (`scan_fused.py`) is the one to use**, and is what CLI activity 3
runs. Both radios see the same advertising packets at the same instant, so they
report the same address — including a rotating private one — and can be merged by
address. That matters because the two backends know different halves of what you
need: only the **dongle** reports the advertising PDU type (`Conn`), which decides
whether a connection-based check can reach the target at all; only **BlueZ** can act
on it. Triaging in one table and acting from the other let the private address rotate
in between, so the verdict no longer applied to the device you touched.

The fused table therefore carries a real **`Conn`** column plus the dongle's richer
`Type`/Fast Pair decoding, and a **`Target`** column holding two independent signals:

- **`FP`** — the device advertises Google Fast Pair (`0xFE2C`), which it only does
  **while in pairing mode**. That is the operational target signal: someone has to put
  the device in that state deliberately, which is also what lets you attribute it — by
  *name*, since a rotating private address can never be tied to an owner by looking at
  it.
- **`RPA`** — `rnd-r` **and** connectable: the precondition of the *mechanism*, i.e.
  there is a private address to unmask and a link on which to do it.

`FP+RPA` is the ready-to-run case. Press **`V`** to hide rows carrying neither signal.
Keeping them apart is deliberate: *reachable* and *yours to test* are different
questions, and one "viable" flag used to conflate them. The dongle is optional: without
it `Conn` reads `?`, no row can show `RPA`, and the table degrades to the BlueZ-only
view.

Columns: address, **Addr** kind, name, RSSI, Fast Pair, Manufacturer, bond state and
number of known services. BlueZ reports only *public* vs *random*, so `rnd-s` /
`rnd-r` / `rnd-n` are derived from the address bits (`exposure.addr_kind`). It gives
less advertising detail than the dongle scan (no PDU type, so **connectable** is
reported as *unknown* rather than guessed) — use the dongle scan for identification
depth, this one for host-side interaction.

**Identity address disclosure (`identity.py`)** — the per-device active action. A
device advertising an RPA should keep its permanent Identity Address secret from
anyone without its IRK; this check tests that.

It opens a plain BLE connection and then looks for a *second* address appearing in
BlueZ's device table under the target's name. **The connection is where the disclosure
happens**: the host resolves the peer's private address and caches its identity address
while processing connection completion, at HCI level, below SMP and GATT. Only if that
produced nothing attributable — typically an RPA advertising no name — does the check
register a `NoInputNoOutput` (Just Works) agent and attempt a **pairing**, where a new
*bond* is the remaining way to tie an address to the peer just contacted. When the
connection alone answers, no pairing is attempted and nothing is left on the target.

**No GATT characteristic is written**, and the Fast Pair service check is advisory and
never gates the flow — which is the point: the disclosure happens in the *host's* device
table, outside the accessory's application layer, so an accessory patched against
CVE-2025-36911 is not protected by that fix here.

Guard-rails, because this one transmits. Three preconditions are refused **without
sending anything**, so a pointless target costs nothing instead of a minute:
- **public** — the identity address is already in the clear;
- **random static** — a permanent identity address in its own right, not a privacy
  feature, so there is nothing to unmask (the check only makes sense against a
  *resolvable* `rnd-r` address);
- **non-connectable** — known from the fused table's `Conn`: no LE link means no SMP
  and nothing to distribute, whatever the check does;
- candidates must be attributable to the target (its name, or a `Paired`/`Bonded`
  line) — unrelated devices discovered meanwhile are counted as ambient noise,
  reported, and never touched;
- a row that is `RPA` **without** `FP` is reachable but of **unverified ownership** —
  a rotating address identifies nobody, so in a non-isolated environment such a row is
  most likely a stranger's phone or watch. The menu says so before you act. Only run
  this against a device you put in pairing mode yourself;
- host-side traces are cleaned up automatically (`remove`), and only entries this run
  introduced are ever removed. A bond may remain **on the target device** and must be
  cleared there by hand; the report says so.

### Batch scan (scriptable)
Non-interactive scan for a fixed duration, exports to `csv/output_<epoch>.csv`.

```bash
.venv310/bin/python scan.py --timeout 8
```

### Central / GATT client
Connects to a target (by name or address), discovers services and characteristics
and reads the readable ones. Generic: works with any device.

```bash
.venv310/bin/python client.py --name "MyDevice"
.venv310/bin/python client.py --address AA:BB:CC:DD:EE:FF --subscribe
```

### Peripheral / device emulation
The dongle advertises and exposes a custom service (RX write, TX notify with echo,
counter). Useful to test apps or a second central.

```bash
.venv310/bin/python emulate.py --name "TestDevice"
```

To verify it: connect from your phone with the **nRF Connect** app, or with
`client.py` from a second dongle.

## Layout

```
cli.py          interactive menu (entry point)
scan_live.py    live BLE scan: full-screen table + CSV
scan_classic.py live Bluetooth Classic (BR/EDR) scan via host adapter + CSV
scan_le.py      live BLE scan via host adapter (BlueZ), subclasses the Classic scanner
scan_fused.py   both BLE radios at once, merged by address (dongle Conn + BlueZ action)
identity.py     active identity-address (BDADDR) disclosure check, host-side
livetable.py    shared full-screen curses live-table UI
scan.py         batch BLE scan -> CSV
client.py       central: connect + GATT exploration
emulate.py      peripheral: advertising + GATT server
bledev/         shared BLE utilities
  device.py       open dongle + port autodetection
  manufacturers.py Company Identifier lookup from manufact.yaml
manufact.yaml   Company Identifiers database (Bluetooth SIG)
```

## Complementary paths (when NOT to use this dongle)

- **nRF Sniffer** — reflashing the dongle with the *nRF Sniffer for Bluetooth LE*
  firmware turns it into a passive sniffer for **Wireshark**, ideal to debug real
  traffic between two devices. It is a different firmware from Connectivity: after
  reflashing, the dongle no longer works with blatann until you restore the
  Connectivity firmware.
- **Bleak** — cross-platform Python library that uses the host BLE stack (BlueZ on
  Linux). **Central** role only, but no dedicated hardware needed: handy as a
  host-side fallback when the dongle is not available.
