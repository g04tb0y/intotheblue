# Test plan — Redmi Buds 6 Play (Fast Pair identity-disclosure validation)

Executed 2026-08-28 against a Redmi Buds 6 Play accessory. This document is the
test plan **and** its results, because the run resolved the one open question the
plan existed for: whether `identity.extract` discloses a real device's identity
address. It does not, on this device — and neither does the reference tool.

## Result in one line

On the Redmi Buds 6 Play, the identity-address disclosure does **not** reproduce:
the host opens an unauthenticated LE connection and reads full GATT, but a plain
Just Works bond is refused (`org.bluez.Error.AuthenticationCanceled`) and **no
permanent identity address ever enters the BlueZ device table**. `intotheblue`'s
`identity.extract` and `Whisper_Bully` (`wb.py`, the reference tool, with `sudo` +
`bleak`) fail **identically** — so this is a device/mechanism property, not an
`intotheblue` bug.

## Target confirmed

Fast Pair accessory, connectable, resolvable private address (RPA, `rnd-r`). The
RPA rotated across the session — every one of these resolved to the name
"Redmi Buds 6 Play" on GATT connect, which is what attributes them to the buds:

`60:CD:ED:7A:B3:74` · `40:EF:01:90:13:6F` · `53:45:B3:DD:E1:CA` ·
`6D:AC:AB:FD:52:5E` · `54:55:4B:02:2D:2B` · `7F:43:3A:37:00:E6`

GATT (unauthenticated, no bond): GAP, a vendor service `0xaf00`, and the Google
Fast Pair service `0xfe2c` with the Key-Based Pairing characteristics
`fe2c1233`…`fe2c1237`. Device name "Redmi Buds 6 Play" is readable over GATT
without bonding.

---

## Phase 0 — sanity (buds sealed) — PASS

- `dmesg | grep xhci`: clean, no `not part of TD` USB fault.
- `hci0`: UP RUNNING; `bluetooth.service` active.
- Dongle (`bledev.open_device`): opens and closes clean.
- **T0.2 observation pipeline** — an emulated Fast Pair beacon (dongle transmits
  `0xFE2C` + a 3-byte Model ID, connectable; read back with `scan_le.LeScanner`
  directly, never the fused scanner) decoded correctly: `FastPair=Yes`, protocol
  "Google Fast Pair", name via scan response. Proves scan-side decoding before the
  real target arrived. (Harness: `fp_advertiser.py`, scratchpad, not committed.)

## Phase 1 — disclosure check (virgin buds, in pairing mode)

- **T1.3 fused triage** — exactly one Fast Pair candidate, `Target=FP+RPA`,
  `Conn=YES`, RSSI −38, strong vs neighbours: the "ready" case.
- **T1.2 exposure (passive)** — reported `Fast Pair (FE2C) advertised
  (non-discoverable)` / verdict "medium". This is the **fused Model-ID masking
  caveat**: `scan_fused.snapshot` lets the BlueZ `fast_pair="Yes"` win the `or`, so
  the dongle's decoded Model ID (and the discoverable state) is masked. The buds
  *were* in discoverable pairing mode; the passive verdict understates it. To see
  the Model ID, use activity 1 (dongle scan).
- **T1.4 disclosure (active)** — connection established (~4.8 s, stays up), GATT
  resolved, name "Redmi Buds 6 Play". Just Works bond → `AuthenticationCanceled`,
  `Paired: no` / `Bonded: no`. No second address attributable to the target. A
  tight connect→pair diagnostic confirmed the connect is fast and the link stays
  **Connected: yes** through the failed pairing — so this is the device **actively
  refusing the bond**, not a closed pairing-mode window or a dropped link.

## Phase 2 — corrected scenario (buds bonded to a phone)

The realistic target for this technique is a device **already bonded to a phone**,
advertising its reconnect RPA — not a virgin device in pairing mode. Re-tested in
that state:

- Same outcome: connection OK, `AuthenticationCanceled`, and a **manual** WB-style
  device-table scan found **no** same-name / different-address entry — only the
  RPA `54:55:4B:02:2D:2B` "Redmi Buds 6 Play" (random) and the pre-existing Jabra.
- **Whisper_Bully cross-check** (`sudo python3 wb.py -s 15`, buds kept paired to the
  phone): found the RPA `7F:43:3A:37:00:E6`, connected, "Fast Pair service
  confirmed", pairing produced no `Bonded: yes`, device list showed no permanent
  address → **`Failed to extract`**.

---

## Why it fails (mechanism)

Resolving an RPA to its identity address **requires the peer's IRK**. The host
obtains the IRK only when an SMP pairing/bond **completes** (identity distribution
phase). This host has never bonded these buds, so it has no IRK and cannot resolve
the RPA on connection — which is exactly what BLE privacy (Core spec) prescribes,
and exactly what we observe: connection completes, GATT resolves, **nothing is
disclosed**. The buds refuse the Just Works bond that would distribute the IRK, so
that path yields nothing either. The device being bonded to a *phone* does not help
— the phone holds the IRK, not our host.

This contradicts `Whisper_Bully`'s stated Stage-1 root cause ("BlueZ resolves the
RPA to the identity address at `LL_CONNECTION_COMPLETE`, before any GATT, works on
fully patched devices"). That claim did not reproduce here, and WB itself failed to
extract — consistent with the theory above.

## Positive control — pipeline validated (2026-08-28)

The obvious caveat was "no known-vulnerable device, so is the pipeline even capable of
disclosing?". Resolved with an **in-house** positive control instead of a purchase:
`emulate.py --positive-control` makes the nRF52 dongle advertise a Resolvable Private
Address and accept an unauthenticated Just Works bond, distributing its IRK + identity
(blatann 0.6.0: `set_privacy_settings(resolvable_address=True)` +
`set_security_params(bond=True, io_capabilities=NONE)`; the peripheral always sets
`kdist_own.id_key`). The two BLE radios are independent, so the dongle is the peripheral
and the host adapter the central on one machine — no phone required.

`identity.extract` against the emulated RPA (`42:3A:1F:98:F3:D3`, name "PosCtrlBuds"):

    Pairing attempt   paired
    Bond              target reported Bonded: yes
    Verdict           IDENTITY ADDRESS DISCLOSED — CE:C1:1B:51:DD:23 (random static)

That is the dongle's real identity address, correctly attributed (new bond + name). So
`intotheblue`'s check and the whole disclosure pipeline are **proven correct in this
environment** — which upgrades the Redmi result: its negative is a genuine **device
property** (it refuses the bond), not a tooling/stack defect.

## Caveats / open

- **Real-world confirmation (optional).** The in-house control validates the pipeline;
  confirming on a real Fast Pair accessory that does *not* enforce the bond gate would
  additionally show such a device exists in the wild, but is a nice-to-have.

## Backlog enabled by this device (short)

1. **Fast Pair Model ID → product name** — resolve the 3-byte Model ID against
   Google's metadata registry to print "Redmi Buds 6 Play" instead of the raw ID.
   Enriches `scan_live` / the FastPair column. Passive, high identification value.
2. **Fused Model-ID masking fix** — in `scan_fused.snapshot`, prefer the dongle's
   decoded Fast Pair Model ID over BlueZ's bare "Yes" so the fused table and the
   passive exposure verdict don't understate a discoverable target.
3. **Emulation/cloning** — extend `fp_advertiser.py` to clone a captured real Fast
   Pair advertisement (FE2C + exact Model ID) to exercise seekers/phones and our
   decoding without the device. First step toward the roadmap's cloning direction.
4. **Positive-control target** — obtain a known-vulnerable Fast Pair accessory to
   confirm the disclosure path works at all in this environment (turns caveat #1
   into a measured result).
5. **Service-UUID → SIG name** in the dongle scan table (already in
   `scan_le._SERVICE_LABELS`; surface it as a column).
6. **TX Power / OUI vendor lookup** — parse TX Power from raw advertising records;
   IEEE OUI DB for public addresses (N/A for the buds' RPA, useful for neighbours).

## Files exercised

`scan_fused.py` (triage, `target_note`, fusion) · `scan_le.py` (BlueZ LE scan) ·
`identity.py` (`extract`, verdicts) · `exposure.py` (passive) · `bledev/` (dongle) ·
`client.py`/`capabilities.py` (GATT, not the blocker here). Reference:
`/opt/repo/Whisper_Bully/wb.py` (`extract_bdaddr`, README Stage 1).
