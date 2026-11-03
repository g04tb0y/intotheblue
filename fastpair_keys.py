"""Resolve a Fast Pair **anti-spoofing public key** by 24-bit Model ID.

The Key-based Pairing handshake (`fastpair.build_kbp_request`) needs the Provider's
anti-spoofing *public* key (a 64-byte secp256r1 point, X‖Y) to run ECDH. For the in-house
emulated Provider we generate the key pair ourselves; to run the handshake against a
**real** device (e.g. the Redmi Buds, Model ID ``FB9B72``) we must obtain *its* public
key by Model ID. The key is public by nature; this module resolves it from a layered
chain, first hit wins:

  1. a **local file** ``fastpair_keys.yaml`` (operator-supplied / cache), keyed by Model
     ID — same offline-first pattern as ``bledev/fastpair.py`` + ``fastpair_models.yaml``;
  2. an **opt-in Google lookup** against the device-metadata endpoint, which is also
     cached back into the local file so later runs are offline.

Posture: the "offline/non-phoning-home" default is for telemetry and the *passive* name
lookup — it does **not** forbid a network call a test needs. The Google path is therefore
**opt-in** (``allow_network=True`` **and** an operator-supplied API key) so the offline
default is preserved when wanted. We deliberately do **not** ship a Google API key: the
GMS-embedded key is Google's, and using the endpoint is the operator's responsibility and
for authorized testing only (subject to Google's ToS). Kept free of any blatann import so
it is unit-testable headless and usable from any front-end, like ``bledev/fastpair.py``.

Endpoint shape verified from Chromium ``ash/quick_pair/repository/fast_pair/
device_metadata_fetcher.cc`` (2026-11-03): the model id is sent as a **decimal** int,
the API key is a ``key=`` query param, and the response (``GetObservedDeviceResponse``)
carries the key at ``device.anti_spoofing_key_pair.public_key`` (JSON:
``device.antiSpoofingKeyPair.publicKey``), base64-encoded.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import urllib.parse
import urllib.request

import yaml

# fastpair_keys.yaml lives at the project root, next to fastpair_models.yaml.
_DEFAULT_YAML = (os.environ.get("INTOTHEBLUE_FASTPAIR_KEYS")
                 or os.path.join(os.path.dirname(__file__), "fastpair_keys.yaml"))
_API_KEY_ENV = "INTOTHEBLUE_FASTPAIR_API_KEY"

# Google device-metadata endpoint (Chromium device_metadata_fetcher.cc). Model id is a
# DECIMAL int; `alt=json` asks for JSON (Chromium uses alt=proto, but JSON needs no protobuf
# dependency). mode=MODE_RELEASE is the production mode.
_GOOGLE_URL = ("https://nearbydevices-pa.googleapis.com/v1/device/{model_id}"
               "?key={api_key}&mode=MODE_RELEASE&alt=json")
# An Android-like UA; the endpoint expects GMS-style clients.
_USER_AGENT = ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/120.0.0.0 Mobile Safari/537.36")

# Cache: 24-bit Model ID (int) -> 64-byte public key. None = not loaded yet.
_lookup: dict[int, bytes] | None = None


def _norm(model_id) -> int | None:
    """Normalise a Model ID (int / ``'FB9B72'`` / ``'0xFB9B72'``) to its 24-bit int.

    Returns ``None`` for the ``'Yes'`` sentinel / empty / product names, so a raw
    ``fast_pair`` field can be passed straight through.
    """
    if isinstance(model_id, bool):
        return None
    if isinstance(model_id, int):
        return model_id & 0xFFFFFF
    if isinstance(model_id, str):
        s = model_id.strip().lower()
        if s.startswith("0x"):
            s = s[2:]
        try:
            return int(s, 16) & 0xFFFFFF
        except ValueError:
            return None
    return None


def _normalize_pubkey(raw: bytes) -> bytes | None:
    """Accept a 64-byte ``X‖Y`` point, or a 65-byte ``0x04‖X‖Y`` one (strip the tag)."""
    if len(raw) == 64:
        return raw
    if len(raw) == 65 and raw[0] == 0x04:
        return raw[1:]
    return None


def _decode_key_text(text: str) -> bytes | None:
    """Decode a public key written as hex or base64 into normalised 64 bytes."""
    s = text.strip()
    # Try hex first (128 or 130 hex chars), then base64.
    try:
        return _normalize_pubkey(bytes.fromhex(s))
    except ValueError:
        pass
    try:
        return _normalize_pubkey(base64.b64decode(s, validate=True))
    except (binascii.Error, ValueError):
        return None


def _load_keys(path: str | None = None) -> dict[int, bytes]:
    """Load ``fastpair_keys.yaml`` once and index Model ID (int) -> 64-byte key."""
    global _lookup
    path = path or _DEFAULT_YAML
    if _lookup is None:
        try:
            with open(path, "r") as file:
                data = yaml.safe_load(file) or {}
        except FileNotFoundError:
            data = {}
        entries = data.get("keys") if isinstance(data, dict) else None
        out: dict[int, bytes] = {}
        for key, value in (entries or {}).items():
            norm = _norm(key)
            if norm is None or not value:
                continue
            decoded = _decode_key_text(str(value))
            if decoded is not None:
                out[norm] = decoded
        _lookup = out
    return _lookup


def _cache_key(model_id: int, pubkey: bytes, path: str | None = None) -> None:
    """Persist a resolved key into ``fastpair_keys.yaml`` (base64) and the in-memory cache.

    Best-effort: a write failure (read-only FS) must not break resolution.
    """
    path = path or _DEFAULT_YAML
    load_keys(path)
    assert _lookup is not None
    _lookup[model_id] = pubkey
    try:
        try:
            with open(path, "r") as file:
                data = yaml.safe_load(file) or {}
        except FileNotFoundError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("keys", {})
        data["keys"][f"{model_id:06X}"] = base64.b64encode(pubkey).decode("ascii")
        with open(path, "w") as file:
            yaml.safe_dump(data, file, default_flow_style=False)
    except OSError:
        pass


# Public alias so callers outside can prime/inspect the cache if needed.
load_keys = _load_keys


def fetch_from_google(model_id: int, api_key: str, *, timeout: float = 10.0) -> bytes | None:
    """Fetch the anti-spoofing public key from Google's device-metadata endpoint.

    Returns the normalised 64-byte key, or ``None`` on any failure (HTTP error, missing
    key, parse error) — never raises into the caller. Isolated so tests can monkeypatch it.
    """
    url = _GOOGLE_URL.format(model_id=model_id, api_key=urllib.parse.quote(api_key, safe=""))
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (operator opt-in)
            payload = resp.read()
    except Exception:                                               # noqa: BLE001
        return None
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError):
        return None
    return _extract_pubkey(obj)


def _extract_pubkey(obj) -> bytes | None:
    """Pull the base64 anti-spoofing public key out of a GetObservedDeviceResponse JSON.

    Tries the known proto3-JSON field paths (lowerCamelCase) and the raw proto names.
    """
    device = obj.get("device") if isinstance(obj, dict) else None
    if not isinstance(device, dict):
        return None
    pair = device.get("antiSpoofingKeyPair") or device.get("anti_spoofing_key_pair")
    b64 = None
    if isinstance(pair, dict):
        b64 = pair.get("publicKey") or pair.get("public_key")
    if not b64:
        return None
    try:
        return _normalize_pubkey(base64.b64decode(b64))
    except (binascii.Error, ValueError):
        return None


def resolve(model_id, *, allow_network: bool = False, api_key: str | None = None) -> dict:
    """Resolve a Model ID to its anti-spoofing public key via the layered chain.

    Returns ``{"model_id", "public_key" (bytes|None), "source" ("file"|"google"|None),
    "detail"}``. The Google step runs only when ``allow_network`` and an API key is
    available (arg or ``$INTOTHEBLUE_FASTPAIR_API_KEY``); a successful fetch is cached.
    """
    norm = _norm(model_id)
    out = {"model_id": (f"{norm:06X}" if norm is not None else str(model_id)),
           "public_key": None, "source": None, "detail": ""}
    if norm is None:
        out["detail"] = "not a 24-bit Model ID"
        return out
    keys = _load_keys()
    if norm in keys:
        out.update(public_key=keys[norm], source="file",
                   detail="resolved from local fastpair_keys.yaml")
        return out
    if allow_network:
        api_key = api_key or os.environ.get(_API_KEY_ENV)
        if not api_key:
            out["detail"] = (f"not in fastpair_keys.yaml and no API key "
                             f"(${_API_KEY_ENV}) for the Google lookup")
            return out
        pub = fetch_from_google(norm, api_key)
        if pub is not None:
            _cache_key(norm, pub)
            out.update(public_key=pub, source="google",
                       detail="fetched from Google device-metadata and cached")
            return out
        out["detail"] = "Google lookup returned no anti-spoofing key (unknown model / v1 / error)"
        return out
    out["detail"] = "not in fastpair_keys.yaml (enable the Google lookup to fetch it)"
    return out


def resolve_public_key(model_id, *, allow_network: bool = False,
                       api_key: str | None = None) -> bytes | None:
    """The 64-byte public key for a Model ID, or ``None``. Thin wrapper over `resolve`."""
    return resolve(model_id, allow_network=allow_network, api_key=api_key)["public_key"]
