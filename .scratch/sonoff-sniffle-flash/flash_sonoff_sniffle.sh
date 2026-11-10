#!/usr/bin/env bash
#
# flash_sonoff_sniffle.sh — flash the Sniffle sniffer firmware onto a
# Sonoff ZBDongle-P (ITead Zigbee 3.0 USB Dongle Plus, CC2652P radio).
#
# WHY THIS EXISTS
#   The ZigStar bring-up (NOTES, 2026-10-15) is blocked on the host->CC2652P
#   TX line. The ZBDongle-P sidesteps all of that: its onboard CP210x bridge
#   is wired straight to the CC2652P UART and BSL entry is automatic (DTR/RTS),
#   so Sniffle flashes over plain USB with no soldering / no external FTDI.
#
#   The one real decision is the firmware VARIANT, which depends on the USB
#   bridge chip (CP2102N -> 2 Mbps full-rate build; plain CP2102 -> 921600
#   "_1M" build). Flashing the wrong one gives garbled decode, and worst case
#   can lock the bootloader (recovery via JTAG/SWD). So this wrapper:
#     1. finds the dongle's serial port (prefers /dev/serial/by-id),
#     2. best-effort detects the bridge chip to pick the variant,
#     3. picks the matching .hex,
#     4. prints the plan and asks for confirmation,
#     5. runs JelmerT's cc2538-bsl with --bootloader-sonoff-usb,
#     6. tells you about the known post-flash reset bug.
#
# This is a scratch helper, not a committed tool. Driven against real hardware.
#
# Usage:
#   ./flash_sonoff_sniffle.sh [options]
#
# Options:
#   -p, --port PATH       serial port (default: autodetect)
#   -d, --hex-dir DIR     dir holding the Sniffle .hex files (default: ./fw)
#   -b, --bsl PATH        path to cc2538-bsl.py (default: ./cc2538-bsl/cc2538-bsl.py)
#   -V, --variant V       force firmware variant: "full" (CP2102N, 2M) or
#                         "1m" (plain CP2102, 921600). Default: autodetect.
#   -f, --hex PATH        flash this exact .hex (overrides variant selection)
#   -y, --yes             skip the confirmation prompt
#   -n, --dry-run         print what would run, do not flash
#   -h, --help            this help
#
# Expected firmware filenames in --hex-dir (from the Sniffle releases page,
# github.com/nccgroup/Sniffle -> Releases):
#   sniffle_cc1352p1_cc2652p1.hex      (CP2102N, full 2 Mbps)
#   sniffle_cc1352p1_cc2652p1_1M.hex   (plain CP2102, 921600)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PORT=""
HEX_DIR="$HERE/fw"
# Upstream JelmerT/cc2538-bsl is now a package: the script lives at
# cc2538-bsl/cc2538_bsl/cc2538_bsl.py (not the old top-level cc2538-bsl.py).
# It has no package-relative imports, so running the file directly works.
BSL="$HERE/cc2538-bsl/cc2538_bsl/cc2538_bsl.py"
# cc2538-bsl needs pyserial + intelhex. The repo's pinned 3.10 venv already
# has both; the Kali system python3 does not. Prefer the venv interpreter.
PY="$(command -v python3)"
for cand in "$HERE/../../.venv310/bin/python" /opt/repo/intotheblue/.venv310/bin/python; do
  [[ -x "$cand" ]] && { PY="$cand"; break; }
done
VARIANT=""           # "", "full", or "1m"
HEX_OVERRIDE=""
ASSUME_YES=0
DRY_RUN=0

HEX_FULL="sniffle_cc1352p1_cc2652p1.hex"
HEX_1M="sniffle_cc1352p1_cc2652p1_1M.hex"

die() { echo "error: $*" >&2; exit 1; }
note() { echo "[*] $*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--port)     PORT="$2"; shift 2;;
    -d|--hex-dir)  HEX_DIR="$2"; shift 2;;
    -b|--bsl)      BSL="$2"; shift 2;;
    -V|--variant)  VARIANT="$2"; shift 2;;
    -f|--hex)      HEX_OVERRIDE="$2"; shift 2;;
    -y|--yes)      ASSUME_YES=1; shift;;
    -n|--dry-run)  DRY_RUN=1; shift;;
    -h|--help)     sed -n '2,60p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0;;
    *)             die "unknown option: $1 (try --help)";;
  esac
done

# --- 1. locate the serial port -------------------------------------------------
autodetect_port() {
  local candidates=()
  if [[ -d /dev/serial/by-id ]]; then
    # Sonoff usually enumerates as an ITead/Silicon Labs CP210x bridge.
    while IFS= read -r link; do
      candidates+=("$link")
    done < <(ls /dev/serial/by-id/ 2>/dev/null \
               | grep -Ei 'sonoff|itead|cp210|silicon' || true)
    if [[ ${#candidates[@]} -eq 1 ]]; then
      echo "/dev/serial/by-id/${candidates[0]}"; return 0
    elif [[ ${#candidates[@]} -gt 1 ]]; then
      echo "multiple by-id candidates, pass --port:" >&2
      printf '   /dev/serial/by-id/%s\n' "${candidates[@]}" >&2
      return 1
    fi
  fi
  # fallback: a single ttyUSB
  local tty=(/dev/ttyUSB*)
  if [[ -e "${tty[0]}" && ${#tty[@]} -eq 1 ]]; then
    echo "${tty[0]}"; return 0
  fi
  return 1
}

if [[ -z "$PORT" ]]; then
  PORT="$(autodetect_port)" || die "could not autodetect the port; pass --port"
fi
[[ -e "$PORT" ]] || die "port does not exist: $PORT"
note "port: $PORT"

# --- 2. detect the USB bridge chip (CP2102 vs CP2102N) -------------------------
# Both are Silicon Labs VID 10c4, often same PID ea60, so we read the USB
# product string from sysfs (and from the by-id link name as a backup).
detect_bridge() {
  local hint=""
  # by-id link name sometimes carries the product string
  case "$PORT" in
    *CP2102N*|*cp2102n*) echo "cp2102n"; return 0;;
  esac
  # walk sysfs for the product string of the device backing this tty
  local base dev prod
  base="$(basename "$(readlink -f "$PORT")")"            # e.g. ttyUSB0
  dev="/sys/class/tty/$base/device"
  if [[ -e "$dev" ]]; then
    # climb to the USB device node that has a 'product' attribute
    local d="$dev"
    for _ in 1 2 3 4 5 6; do
      d="$(readlink -f "$d/..")" || break
      if [[ -r "$d/product" ]]; then
        prod="$(cat "$d/product" 2>/dev/null || true)"
        hint="$prod"
        break
      fi
    done
  fi
  case "$hint" in
    *CP2102N*) echo "cp2102n"; return 0;;
    *CP2102*)  echo "cp2102";  return 0;;   # plain CP2102
    *)         echo "unknown"; return 0;;
  esac
}

if [[ -z "$VARIANT" && -z "$HEX_OVERRIDE" ]]; then
  bridge="$(detect_bridge)"
  note "bridge chip: $bridge"
  case "$bridge" in
    cp2102n) VARIANT="full";;
    cp2102)  VARIANT="1m";;
    *)
      echo "could not determine the USB bridge chip." >&2
      echo "  CP2102N  -> re-run with --variant full" >&2
      echo "  CP2102   -> re-run with --variant 1m" >&2
      echo "Check with:  lsusb -v -d 10c4: | grep -i iProduct" >&2
      die "bridge unknown; choose the variant explicitly"
      ;;
  esac
fi

# --- 3. choose the firmware .hex ----------------------------------------------
if [[ -n "$HEX_OVERRIDE" ]]; then
  HEX="$HEX_OVERRIDE"
else
  case "$VARIANT" in
    full) HEX="$HEX_DIR/$HEX_FULL";;
    1m)   HEX="$HEX_DIR/$HEX_1M";;
    *)    die "invalid --variant '$VARIANT' (use full|1m)";;
  esac
fi
[[ -f "$HEX" ]] || die "firmware not found: $HEX
  Download it from the Sniffle releases page into: $HEX_DIR
    full (CP2102N): $HEX_FULL
    1m   (CP2102):  $HEX_1M"
note "firmware: $HEX  (variant: ${VARIANT:-override})"

# --- 4. sanity-check the flasher ----------------------------------------------
[[ -f "$BSL" ]] || die "cc2538-bsl not found: $BSL
  git clone https://github.com/JelmerT/cc2538-bsl (into $HERE)
  (upstream script now lives at cc2538-bsl/cc2538_bsl/cc2538_bsl.py)"
note "python: $PY"

CMD=("$PY" "$BSL" -p "$PORT" --bootloader-sonoff-usb -ewv "$HEX")

echo
echo "About to flash:"
echo "    ${CMD[*]}"
echo
echo "  !! brick risk: wrong variant can lock the bootloader (JTAG/SWD recovery)."
echo "     full = CP2102N (2 Mbps) | 1m = plain CP2102 (921600). You picked: ${VARIANT:-override}"
echo

if [[ $DRY_RUN -eq 1 ]]; then
  note "dry-run: not flashing."
  exit 0
fi

if [[ $ASSUME_YES -ne 1 ]]; then
  read -r -p "Proceed? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { note "aborted."; exit 1; }
fi

# --- 5. flash -----------------------------------------------------------------
set +e
"${CMD[@]}"
rc=$?
set -e

echo
if [[ $rc -eq 0 ]]; then
  note "flash + verify OK."
else
  echo "[!] cc2538-bsl exited $rc."
  echo "    Known bug (cc2538-bsl, as of 2025-01-10): it may fail to RESET the"
  echo "    CC2652P on the Sonoff after a successful write. If erase/write/verify"
  echo "    lines above look good, the flash is fine — just UNPLUG and REPLUG the"
  echo "    dongle. (Kismet also notes removing python3-magic can help.)"
fi

echo
note "verify Sniffle responds (from the Sniffle repo):"
echo "    python3 python_cli/sniff_receiver.py -s $PORT"
