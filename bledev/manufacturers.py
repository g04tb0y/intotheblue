"""Resolve a BLE Company Identifier to a vendor name.

Prefers the authoritative SIG checkout (`bledev.sig`, from BluetoothSIGPublic) and
falls back to the bundled `manufact.yaml` snapshot when it is absent.
"""
from __future__ import annotations

import os

import yaml

from . import sig

# manufact.yaml lives at the project root, one level above this package
_DEFAULT_YAML = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manufact.yaml")

# Cache: value (int) -> name (str)
_lookup: dict[int, str] | None = None


def load_manufacturer_data(path: str = _DEFAULT_YAML) -> dict[int, str]:
    """Load manufact.yaml once and index it by company id."""
    global _lookup
    if _lookup is None:
        with open(path, "r") as file:
            data = yaml.safe_load(file)
        _lookup = {entry["value"]: entry["name"] for entry in data["company_identifiers"]}
    return _lookup


def get_manufacturer(mnf_id: int) -> str:
    """Return '<id> <name>' for a company id, or 'N/A' if unknown.

    SIG registry first (up to date), then the bundled snapshot.
    """
    name = sig.company_name(mnf_id) or load_manufacturer_data().get(mnf_id)
    if name is None:
        return "N/A"
    return f"{mnf_id} {name}"
