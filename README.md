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
