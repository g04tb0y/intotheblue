"""Authoritative Bluetooth SIG assigned numbers (company IDs + UUIDs).

Reads the official `BluetoothSIGPublic/assigned_numbers/` YAML dump the maintainer
checked out at the repo root, so vendor and UUID naming track the current SIG
registry instead of small hand-curated dicts or a bundled snapshot:

    company_identifiers.yaml   4000+ Company IDs (manufacturer data / DIS PnP)
    uuids/member_uuids.yaml    vendor **member** service UUIDs (0xFE2C = Google, …)
    uuids/service_uuids.yaml   GATT services (0x180F = Battery, …)
    uuids/characteristic_uuids.yaml, descriptors.yaml

Every lookup degrades gracefully: if the SIG checkout is absent, the maps load
empty and callers fall back to their existing behaviour. No blatann import, so the
BlueZ-only surfaces can use it without pulling in the dongle driver.
"""
from __future__ import annotations

import os

import yaml

_SIG_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "BluetoothSIGPublic", "assigned_numbers"
)

# Lazily loaded, cached maps (int -> name)
_companies: dict[int, str] | None = None
_members: dict[int, str] | None = None
_services: dict[int, str] | None = None
_characteristics: dict[int, str] | None = None
_uuid_names: dict[int, str] | None = None


def available() -> bool:
    """Whether the SIG checkout is present."""
    return os.path.isdir(_SIG_ROOT)


def _load_list(rel_path: str, key: str, id_field: str) -> dict[int, str]:
    """Index one SIG YAML (`key` -> list of {id_field, name}) as int -> name."""
    path = os.path.join(_SIG_ROOT, rel_path)
    try:
        with open(path, "r") as file:
            data = yaml.safe_load(file) or {}
    except (FileNotFoundError, NotADirectoryError):
        return {}
    out: dict[int, str] = {}
    for entry in (data.get(key) or []):
        value, name = entry.get(id_field), entry.get("name")
        if isinstance(value, int) and name:
            out[value] = str(name)
    return out


def companies() -> dict[int, str]:
    global _companies
    if _companies is None:
        _companies = _load_list(
            "company_identifiers/company_identifiers.yaml", "company_identifiers", "value"
        )
    return _companies


def members() -> dict[int, str]:
    global _members
    if _members is None:
        _members = _load_list("uuids/member_uuids.yaml", "uuids", "uuid")
    return _members


def services() -> dict[int, str]:
    global _services
    if _services is None:
        _services = _load_list("uuids/service_uuids.yaml", "uuids", "uuid")
    return _services


def characteristics() -> dict[int, str]:
    global _characteristics
    if _characteristics is None:
        _characteristics = _load_list("uuids/characteristic_uuids.yaml", "uuids", "uuid")
    return _characteristics


def company_name(cid: int) -> str | None:
    """Registered company name for a 16-bit Company Identifier, or None."""
    return companies().get(cid)


def member_name(u16: int) -> str | None:
    """Registered company behind a 16-bit **member** service UUID (e.g. 0xFE2C), or None."""
    return members().get(u16)


def uuid_name(u16: int) -> str | None:
    """Human name for any advertised/known 16-bit UUID.

    Merges services, member services, characteristics and descriptors; a GATT
    service name wins over a member/characteristic one for the same value.
    """
    global _uuid_names
    if _uuid_names is None:
        merged: dict[int, str] = {}
        merged.update(characteristics())
        merged.update(members())
        merged.update(services())  # services take precedence
        _uuid_names = merged
    return _uuid_names.get(u16)
