# Sonoff ZBDongle-P — Sniffle flashing helper

Wrapper around JelmerT's `cc2538-bsl` to flash the **Sniffle** connection-following
sniffer onto a **Sonoff ZBDongle-P** (ITead Zigbee 3.0 USB Dongle Plus, CC2652P radio).

Chosen over the ZigStar path because the ZBDongle-P needs **no soldering and no external
FTDI**: its onboard CP210x bridge is wired straight to the CC2652P UART and BSL entry is
automatic over DTR/RTS. (The ZigStar host→CC2652P TX line is blocked — see NOTES
2026-10-15.)

## Layout this script expects
```
sonoff-sniffle-flash/
  flash_sonoff_sniffle.sh
  fw/
    sniffle_cc1352p1_cc2652p1.hex       # CP2102N  -> full 2 Mbps
    sniffle_cc1352p1_cc2652p1_1M.hex    # plain CP2102 -> 921600
  cc2538-bsl/
    cc2538_bsl/cc2538_bsl.py             # git clone JelmerT/cc2538-bsl (now a package)
```

## One-time setup
```bash
cd .scratch/sonoff-sniffle-flash
git clone https://github.com/JelmerT/cc2538-bsl   # script: cc2538_bsl/cc2538_bsl.py
mkdir -p fw   # drop the Sniffle release .hex here (github.com/nccgroup/Sniffle -> Releases)
```
The flasher runs cc2538-bsl with the repo's `.venv310` interpreter, which already has
`pyserial` + `intelhex` (the Kali system `python3` does not). No separate pip install.

## Run
```bash
./flash_sonoff_sniffle.sh --dry-run      # see the detected port / bridge / variant / command
./flash_sonoff_sniffle.sh                # flash (asks for confirmation)
```

Key flags: `--port`, `--variant full|1m`, `--hex PATH`, `--yes`, `--dry-run`.
The **variant** is the one real decision — it follows the USB bridge chip. The script
autodetects it; if it can't, pass `--variant` explicitly. Wrong variant = garbled decode
or, worst case, a locked bootloader (JTAG/SWD recovery).

Full procedure and rationale: see `NOTES.md`.
