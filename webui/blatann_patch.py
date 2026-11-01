"""Contained workaround for a blatann GATT-discovery wedge (applied in-process only).

Some peripherals make blatann's characteristic discovery issue a follow-up
`ble_gattc_char_disc` that the SoftDevice rejects with `NrfError.invalid_state`.
blatann raises that inside the driver event thread and only logs it, so its
discovery state machine never completes: the peer stays connected,
`on_discovery_complete` never fires (our `discover_services().wait()` times out),
and `peer.database` is left empty — no GATT at all, even though services were found.
Reproduced live against a real device (two 128-bit vendor services, peer still
connected, zero services committed).

Fix: wrap the discovery event handlers so a raised `NordicSemiException` is treated
as "end of this phase" — the services/characteristics collected so far are kept and
the phase is finalised, so `peer.database` is populated with a (possibly partial)
GATT instead of nothing. This never edits the installed blatann package; it rebinds
methods on the imported classes, is idempotent, and degrades safely (logs and leaves
blatann untouched) if blatann's internals ever stop matching.
"""
from __future__ import annotations

import functools

_PATCHED = False


def apply() -> bool:
    """Patch blatann's characteristic/descriptor discoverers. Returns True if applied."""
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from blatann.gatt import service_discovery as sd
        from pc_ble_driver_py.exceptions import NordicSemiException
    except Exception:                                           # noqa: BLE001
        return False

    ok_status = sd.nrf_types.BLEGattStatusCode.success

    def _guard(cls, method_name: str) -> None:
        """Wrap `cls.method_name` so a NordicSemiException finalises the phase."""
        original = getattr(cls, method_name)

        @functools.wraps(original)
        def guarded(self, *args, **kwargs):
            try:
                return original(self, *args, **kwargs)
            except NordicSemiException as err:
                sd.logger.warning(
                    "blatann %s.%s wedged (%s) — finalising discovery with the "
                    "partial results collected so far (intotheblue workaround)",
                    cls.__name__, method_name, err)
                try:
                    self._on_complete(ok_status)
                except Exception:                               # noqa: BLE001
                    pass
                return None

        setattr(cls, method_name, guarded)

    try:
        _guard(sd._CharacteristicDiscoverer, "_on_characteristic_discovery")
        _guard(sd._CharacteristicDiscoverer, "_discover_characteristics")
        _guard(sd._DescriptorDiscoverer, "_on_descriptor_discovery")
        _guard(sd._DescriptorDiscoverer, "_discover_descriptors")
    except Exception:                                           # noqa: BLE001
        return False

    _PATCHED = True
    return True
