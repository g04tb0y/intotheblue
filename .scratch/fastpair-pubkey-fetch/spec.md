# Spec — Fast Pair anti-spoofing public key: fetch/resolve by Model ID

Status: implemented (headless-validated; live fetch + real-device handshake pending)

> Implemented 2026-11-03 as specced, with these resolved choices: new `boundary="network"`
> value in the event log (recommended option taken); `fastpair_keys.example.yaml` seed
> committed, live `fastpair_keys.yaml` git-ignored. Endpoint verified from Chromium
> `device_metadata_fetcher.cc` (decimal model id, `key=` query param, `alt=json`) and
> `device_metadata.cc` (field `device.antiSpoofingKeyPair.publicKey`). The capture-
> extraction path remains a documented procedure (write into the YAML), not yet a parser.

## Context / why

`fastpair_handshake` (TODO item 2) runs the Fast Pair Key-based Pairing handshake as
Seeker. To build a valid request it needs the **Provider's anti-spoofing _public_ key**
(a 64-byte secp256r1 point, X‖Y) so it can run ECDH and derive the AES key. Today the
only way to supply it is pasting the hex by hand — which only exists for the in-house
emulated Provider (`emulate.py --fast-pair-gatt`, whose key pair we generate).

To make the handshake runnable against **real devices** (e.g. the Redmi Buds 6 Play,
Model ID `FB9B72`) we need a way to obtain their public key by Model ID. The key is
public by nature; the production path is Google's device-metadata service. This is
**in-scope**: the "offline/non-phoning-home" posture is a default for telemetry and the
passive name lookup, not a ban on a network call a test genuinely needs (see NOTES
2026-11-03 posture correction). The network path stays **opt-in** so the offline default
is preserved when the operator wants it.

## Goal

A resolver that, given a 24-bit Model ID, returns the 64-byte anti-spoofing public key,
from a layered chain (first hit wins), and feeds it into the existing handshake form.

## Design

### New module `fastpair_keys.py` (top-level, no blatann)

Parallels `bledev/fastpair.py` (Model-ID→name) and the top-level `fastpair.py` (crypto);
**no blatann import** so it is unit-testable headless and reusable from engine + CLI.
Stdlib `urllib.request` for the network path (no new dependency; `cryptography` already
in). Public surface:

- `resolve_public_key(model_id, *, allow_network=False, api_key=None) -> bytes | None`
  — the chain:
  1. **Local key file** `fastpair_keys.yaml` (repo root, git-ignored like `db/`; a
     committed seed is fine). Same shape/loader pattern as `fastpair_models.yaml`:
     `keys: { <model-id-hex>: <pubkey base64-or-hex> }`. Offline, zero config, and the
     cache that network/capture results are written back into (the registry grows from
     real lookups, like the model registry grows from captures).
  2. **Google metadata lookup** — only if `allow_network` **and** an `api_key` is
     available (`api_key` arg, else env `INTOTHEBLUE_FASTPAIR_API_KEY`). On success,
     **cache** the key into `fastpair_keys.yaml` so later runs are offline. On any failure
     (no key, HTTP error, parse error) return `None` — never raise into the handshake.
  - Returns a normalised 64-byte `X‖Y` (strip a leading `0x04` if the source is 65 bytes).
- `fetch_from_google(model_id, api_key, *, timeout=10) -> bytes | None` — the network
  call, isolated so it can be monkeypatched in tests and skipped entirely offline.
- `_normalize_pubkey(raw: bytes) -> bytes` — accept 64 (X‖Y) or 65 (`04`‖X‖Y) bytes.
- `_load_keys(path) / _norm(model_id)` — mirror `bledev/fastpair.py` exactly.

### Google endpoint (VERIFY AT IMPLEMENTATION TIME)

Grounded but **unverified in detail** — confirm the path/field names before relying on
them (Chromium `device_metadata.cc` parses a `GetObservedDeviceResponse`; its source was
unreachable at spec time, HTTP 503). Known shape from research:

- Host `nearbydevices-pa.googleapis.com`; the gRPC `GetObservedDevice` has a REST
  transcoding roughly `GET /v1/device/<modelId>?mode=MODE_RELEASE&key=<API_KEY>`.
  **Verify** whether `<modelId>` is decimal or hex, and whether the API key goes in the
  `key=` query param or an `X-Goog-Api-Key` header. Send an Android-like `User-Agent`.
- Response: `GetObservedDeviceResponse` → `device` → anti-spoofing public key. Chromium
  treats an **empty** key as Fast Pair v1 and a **present** key as v2+. The field has
  appeared as `antiSpoofingKeyPair.publicKey` / `anti_spoofing_public_key`; the parser
  should try a small set of known JSON paths and base64-decode the first non-empty match.

**API key policy — do NOT hardcode a scraped key.** The GMS-embedded key is Google's; the
operator supplies their own via `INTOTHEBLUE_FASTPAIR_API_KEY`. Without it the Google path
is simply disabled and we fall back to the local file / capture. Document in the module
docstring that using Google's endpoint is the operator's responsibility and is for
authorized testing only (and that it may be subject to Google's ToS).

### Capture-extraction path (procedure, not a new code path for now)

When a key is recovered from a traffic/device capture, the operator writes it into
`fastpair_keys.yaml` keyed by Model ID — same sink the Google fetch caches into. A
dedicated capture parser is a possible later ticket; for now the YAML is the interface.

### Engine + UI + MCP integration (reuse the handshake already built)

- `engine.fastpair_resolve_key(model_id, allow_network=False) -> dict` — thin wrapper over
  `fastpair_keys.resolve_public_key`, returning `{model_id, public_key_hex|None, source:
  "file"|"google"|None}`. Wrapped in `_op_log` with **`boundary="network"`** when it hits
  Google (a new boundary value, so the event log cleanly distinguishes an on-air radio op
  from an off-device network fetch — passive DB/file reads stay unlogged).
- `engine.fastpair_handshake` gains an optional resolution path: accept either
  `provider_public_hex` (as today) **or** a `model_id` (+ `allow_network`); when a Model ID
  is given, call `fastpair_resolve_key` first and use the resolved key. Keep the hex input
  for the emulated-Provider positive control.
- **UI** (`_gatt_tree.html` handshake form): alongside the pubkey hex field, add a
  **Model ID** input (prefilled from the device's advertised `fast_pair` field when it is a
  6-hex Model ID) and an **"allow Google lookup"** checkbox (default off, `active`/network
  badge). Route `POST …/gatt/fastpair-handshake` takes `model_id` + `allow_network` form
  fields; resolves then runs. Surface `source` in the result line ("key from file/Google").
- **MCP**: extend `fastpair_handshake` params (`model_id`, `allow_network`) and add a read-
  ish `fastpair_resolve_key(model_id, allow_network)` tool (marked **network**, not radio).

### Config / files

- `fastpair_keys.yaml` — new, repo root. Git-ignore the live file (like `db/`); optionally
  commit a `fastpair_keys.example.yaml`. Override path with `$INTOTHEBLUE_FASTPAIR_KEYS`.
- `INTOTHEBLUE_FASTPAIR_API_KEY` — operator's Google API key for the opt-in lookup.

## Verification

**Headless (no network, no radio):**
- `resolve_public_key` from a local YAML: hex value and base64 value both → correct 64-byte
  key; unknown Model ID → `None`; `allow_network=False` never calls the network.
- `_normalize_pubkey`: 64-byte passthrough, 65-byte `04`-prefixed → stripped to 64, wrong
  length → `None`/raise as chosen.
- Network path with `fetch_from_google` monkeypatched to a canned `GetObservedDeviceResponse`
  → parsed to 64 bytes **and cached** into a temp YAML (second call is offline).
- End-to-end crypto: feed the resolved key into `fastpair.build_kbp_request` and confirm the
  emulated-Provider round-trip (`parse_kbp_request`/`build_kbp_response`/`parse_kbp_response`)
  still succeeds (reuses the existing `fastpair.py` tests).

**Live (opt-in, real target):**
- With a real `INTOTHEBLUE_FASTPAIR_API_KEY`, resolve `FB9B72` (Redmi) via Google → a
  64-byte key cached to `fastpair_keys.yaml`.
- Run `fastpair_handshake` against the real buds in discoverable pairing mode and record
  whether they accept the request / notify a valid type-0x01 response — the real-device
  test the emulated Provider can only approximate.

## Open questions

- Exact endpoint path (decimal vs hex Model ID), API-key placement, and response field
  name — verify against current Chromium `device_metadata.cc` / an open-source Fast Pair
  client before trusting the parser.
- Whether to add a `boundary="network"` value to `eventlog`/`_op_log` (recommended) or fold
  network fetches under `active` — decide with the maintainer; this spec assumes a new
  `network` boundary for honest logging.

## Sources

- Fast Pair key roles / configuration: https://developers.google.com/nearby/fast-pair/specifications/configuration
- Key-based Pairing procedure (crypto): https://developers.google.com/nearby/fast-pair/specifications/service/gatt
- Chromium device metadata parser (GetObservedDeviceResponse, anti-spoofing key): https://chromium.googlesource.com/chromium/src/+/main/ash/quick_pair/repository/fast_pair/device_metadata.cc
