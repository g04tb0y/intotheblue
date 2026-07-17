from __future__ import annotations
import blatann
from blatann import BleDevice
from blatann.examples import example_utils
import csv
import time
import datetime
import yaml

logger = example_utils.setup_logger(level="INFO")

data = []
epoch_time = int(time.time())
now = datetime.datetime.now()
with open('manufact.yaml', 'r') as file:
    # Load the content as a dictionary
    manufacturer_data = yaml.safe_load(file)

def get_manufacturer(mnf_data):
    #print(manufacturer_data['company_identifiers'])
    for i in manufacturer_data['company_identifiers']:
        #print(i['value'], i['name'])
        if i['value'] == mnf_data:
            return ""+str(i['value']) +" " +i['name']
    return "N/A"
def main(serial_port):
    # Create and open the device
    ble_device = BleDevice(serial_port)
    ble_device.open()

    logger.info("Scanning...")
    # Set scanning for 4 seconds
    ble_device.scanner.set_default_scan_params(timeout_seconds=4)

    # Start scanning and iterate through the reports as they're received
    for report in ble_device.scanner.start_scan().scan_reports:
        if not report.duplicate:
            mnf = ''
            mnf_int = 0
            if report.advertise_data.manufacturer_data:
                mnf_byte = report.advertise_data.manufacturer_data[:2]
                mnf_int = int.from_bytes(mnf_byte, 'little')
                mnf = get_manufacturer(mnf_int)
            print(report.peer_address, report.device_name, report.rssi, mnf )
            data.append([report.peer_address, report.device_name, report.rssi, mnf, now])
            logger.info(report)

    scan_report = ble_device.scanner.scan_report
    print("\n")
    logger.info("Finished scanning. Scan reports by peer address:")
    # Iterate through all the peers found and print out the reports
    for report in scan_report.advertising_peers_found:
        logger.info(report)

    # Clean up and close the device
    ble_device.close()
    print(data)
    with open('csv/output'+'_'+str(epoch_time)+'.csv', 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerows(data)


if __name__ == '__main__':
    #get_manufacturer("mnf_data")
    main("COM5")