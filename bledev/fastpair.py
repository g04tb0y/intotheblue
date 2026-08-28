"""Resolve a Google Fast Pair 24-bit Model ID to a product name (offline).

Discoverable Fast Pair advertising (service ``0xFE2C``) carries a 3-byte (24-bit)
Model ID assigned by Google at registration. There is no authoritative *offline*
dataset published by Google, and the largest community list was taken down, so
this registry is a **locally curated** map that grows from real captures: when a
discoverable Fast Pair beacon is seen (the dongle scan decodes its Model ID) and
the device is attributed (e.g. by its GATT name), record the mapping in
``fastpair_models.yaml``. Identification only — no Fast Pair protocol interaction.

Kept free of any blatann/dongle import so the BlueZ-only surfaces (``scan_le``)
can use it without pulling in the dongle driver, exactly like ``manufacturers``.
"""
from __future__ import annotations

import os

import yaml

# fastpair_models.yaml lives at the project root, one level above this package
_DEFAULT_YAML = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fastpair_models.yaml")

# Cache: 24-bit Model ID (int) -> product name (str)
_lookup: dict[int, str] | None = None


def _norm(model_id) -> int | None:
    """Normalise a Model ID (int, ``'0C4020'`` or ``'0x0C4020'``) to its 24-bit int.

    Returns ``None`` for anything that is not a Model ID — including ``''`` and the
    ``'Yes'`` sentinel used for non-discoverable Fast Pair, so callers can pass the
    raw ``fast_pair`` field straight through.
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


def load_models(path: str = _DEFAULT_YAML) -> dict[int, str]:
    """Load fastpair_models.yaml once and index it by 24-bit Model ID."""
    global _lookup
    if _lookup is None:
        try:
            with open(path, "r") as file:
                data = yaml.safe_load(file) or {}
        except FileNotFoundError:
            data = {}
        entries = data.get("models") if isinstance(data, dict) else None
        out: dict[int, str] = {}
        for key, value in (entries or {}).items():
            norm = _norm(key)
            if norm is not None and value:
                out[norm] = str(value)
        _lookup = out
    return _lookup


def resolve_model(model_id) -> str | None:
    """Product name for a Fast Pair Model ID, or ``None`` if unknown/not a Model ID."""
    norm = _norm(model_id)
    if norm is None:
        return None
    return load_models().get(norm)


def display(fast_pair: str) -> str:
    """Column text for a ``fast_pair`` field: the product name when the Model ID
    resolves, otherwise the raw value (a 6-hex Model ID, ``'Yes'``, or ``''``)."""
    return resolve_model(fast_pair) or fast_pair
