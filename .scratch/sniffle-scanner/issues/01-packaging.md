# 01 — Package the `sniffle` library into the repo

Status: done (2026-11-04, headless) — vendored to top-level `sniffle/`, SDR modules
trimmed (numpy/scipy/SoapySDR), `VENDORED.md` added, `pyserial` pinned; headless import OK.

## Task
Make the Sniffle Python API importable in `.venv310` the repo's way (in-repo package, not
pip-installed), so `sniff.py` can `from sniffle.sniffle_hw import SniffleHW`.

## Steps
- Vendor `.scratch/sonoff-sniffle-flash/Sniffle/python_cli/sniffle/` → top-level `sniffle/`
  (mirror how `bledev/`, `webui/` are in-repo packages). Keep only the package (drop the SDR
  bits if they pull heavy deps we don't need — check imports first; `sniffle_sdr.py`,
  `resampler.py`, `sdr_utils.py` may need numpy/scipy and are not needed for the USB dongle).
- Add `sniffle/VENDORED.md`: upstream `github.com/nccgroup/Sniffle`, tag **v1.11.0**, what was
  trimmed and why, and the "re-vendor on upgrade" note.
- Add `pyserial` to `requirements.txt` if not pinned there (the venv already has it).
- Verify import works headless: `.venv310/bin/python -c "from sniffle.sniffle_hw import SniffleHW; from sniffle.constants import SnifferMode"`.

## Acceptance
Import succeeds in `.venv310` with no hardware and no SDR/numpy dependency pulled in.

## Notes
If trimming the SDR modules breaks `from sniffle import *` or an `__init__` re-export, prefer
keeping the file but guarding the heavy import, over a broken package.
