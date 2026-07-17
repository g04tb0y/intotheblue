"""Risoluzione del Company Identifier BLE dal file manufact.yaml (Bluetooth SIG)."""
from __future__ import annotations

import os

import yaml

# manufact.yaml sta nella root del progetto, un livello sopra questo package
_DEFAULT_YAML = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manufact.yaml")

# Cache: value (int) -> name (str)
_lookup: dict[int, str] | None = None


def load_manufacturer_data(path: str = _DEFAULT_YAML) -> dict[int, str]:
    """Carica manufact.yaml una sola volta e lo indicizza per company id."""
    global _lookup
    if _lookup is None:
        with open(path, "r") as file:
            data = yaml.safe_load(file)
        _lookup = {entry["value"]: entry["name"] for entry in data["company_identifiers"]}
    return _lookup


def get_manufacturer(mnf_id: int) -> str:
    """Ritorna '<id> <nome>' per un company id, o 'N/A' se sconosciuto."""
    lookup = load_manufacturer_data()
    name = lookup.get(mnf_id)
    if name is None:
        return "N/A"
    return f"{mnf_id} {name}"
