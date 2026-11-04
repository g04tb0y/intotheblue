# Vendored `sniffle` Python package

This is an **in-repo vendored copy** of NCC Group's Sniffle host-side Python API, used the
same way as `bledev/` and `webui/` — imported from the working directory, never
`pip install`ed. It is the host driver for the CC2652P (Sonoff ZBDongle-P) flashed with
Sniffle firmware; see `sniff.py` for the bench integration.

## Source

- Upstream: https://github.com/nccgroup/Sniffle
- Firmware flashed on the Sonoff dongle: **v1.11.0** (full / CP2102N 2 Mbps variant).
- Host package vendored from `python_cli/sniffle/` of the matching checkout
  (commit `3a53f5a`, "relay: stop scanning or advertising on SIGINT").
- The flash tooling and the full upstream clone live under
  `.scratch/sonoff-sniffle-flash/`.

## What was trimmed and why

The SDR backend (software-defined-radio capture, e.g. RFNM/SoapySDR, and file replay) pulls
heavy third-party deps — `numpy`, `scipy`, `SoapySDR` — that this bench does not need: we
drive the **USB serial dongle** only. These modules were removed:

- `sniffle_sdr.py`, `channelizer.py`, `resampler.py`, `sdr_utils.py`, `coding_ble.py`

`sniffle_hw.make_sniffle_hw()` imports the SDR backend **lazily**, and only when `serport`
starts with `rfnm` / `file:`. We always pass a real serial device path, so that branch is
never reached and the deleted modules are never imported. `__init__.py`'s `__all__` was
updated to drop the removed module names (so `from sniffle import *` does not fail).

The only runtime dependency of the trimmed package is **pyserial** (pinned in
`requirements.txt`).

## Re-vendoring on upgrade

When upgrading the dongle firmware, re-vendor the matching `python_cli/sniffle/` from the
upstream tag: copy the package, delete the SDR modules above, and re-trim `__init__.py`.
Then re-run the headless import check:

```
.venv310/bin/python -c "from sniffle.sniffle_hw import SniffleHW; from sniffle.constants import SnifferMode"
```

The packet decoder output format is version-sensitive — hardware-validate `sniff.py` after
any re-vendor.
