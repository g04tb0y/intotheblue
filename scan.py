"""Scansione/inventario dei dispositivi BLE nel raggio, con export CSV.

Il dongle nRF52 fa da central passivo: raccoglie i pacchetti di advertising,
risolve il produttore dal Company Identifier e scrive tutto in csv/output_<epoch>.csv.

Uso:
    python scan.py                 # autorileva il dongle
    python scan.py --port /dev/ttyACM0
    python scan.py --timeout 8
"""
from __future__ import annotations

import argparse
import csv
import datetime
import os
import time

from blatann.examples import example_utils

from bledev import get_manufacturer, open_device

logger = example_utils.setup_logger(level="INFO")


def main(port: str | None, timeout_seconds: int) -> None:
    data = []
    epoch_time = int(time.time())
    now = datetime.datetime.now()

    ble_device = open_device(port)

    logger.info("Scanning...")
    ble_device.scanner.set_default_scan_params(timeout_seconds=timeout_seconds)

    # Scorre i report man mano che arrivano
    for report in ble_device.scanner.start_scan().scan_reports:
        if not report.duplicate:
            mnf = ""
            if report.advertise_data.manufacturer_data:
                mnf_byte = report.advertise_data.manufacturer_data[:2]
                mnf_int = int.from_bytes(mnf_byte, "little")
                mnf = get_manufacturer(mnf_int)
            print(report.peer_address, report.device_name, report.rssi, mnf)
            data.append([report.peer_address, report.device_name, report.rssi, mnf, now])
            logger.info(report)

    scan_report = ble_device.scanner.scan_report
    print("\n")
    logger.info("Finished scanning. Scan reports by peer address:")
    for report in scan_report.advertising_peers_found:
        logger.info(report)

    ble_device.close()

    os.makedirs("csv", exist_ok=True)
    out_path = f"csv/output_{epoch_time}.csv"
    with open(out_path, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerows(data)
    logger.info("Wrote %d rows to %s", len(data), out_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default=None, help="Porta seriale del dongle (default: autorilevata)")
    parser.add_argument("--timeout", type=int, default=4, help="Durata scansione in secondi (default: 4)")
    args = parser.parse_args()
    main(args.port, args.timeout)
