#!/usr/bin/env python3
"""Interactive RFCOMM (Bluetooth Classic SPP) serial console.

Runs in the SYSTEM python3 because the project venv's interpreter is built without
AF_BLUETOOTH. A reader thread prints incoming bytes (hex + printable ASCII); the
main loop sends whatever you type. The device must already be PAIRED.

Usage:  python3 spp_console.py <address> <rfcomm-channel>
"""
import socket
import sys
import threading


def _parse(text: str) -> bytes:
    text = text.strip()
    low = text.lower()
    if low.startswith("hex:"):
        return bytes.fromhex(text[4:].replace(" ", ""))
    if low.startswith("text:"):
        return text[5:].encode()
    compact = text.replace(" ", "")
    if compact and len(compact) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in compact):
        return bytes.fromhex(compact)
    return text.encode()


def main() -> int:
    address = sys.argv[1]
    channel = int(sys.argv[2])

    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.settimeout(15)
    try:
        sock.connect((address, channel))
    except OSError as err:
        print(f"  RFCOMM connect failed: {err}")
        return 1
    sock.settimeout(None)
    print(f"  Connected to {address} on RFCOMM channel {channel}.")
    print("  Type to send (hex:.. / text:.. / auto), blank line to exit.")

    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                data = sock.recv(512)
            except OSError:
                break
            if not data:
                print("\n  << (peer closed the channel)")
                stop.set()
                break
            printable = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
            print(f"  << {data.hex(' ')}  |{printable}|")

    threading.Thread(target=reader, daemon=True).start()

    try:
        while not stop.is_set():
            line = input("  >> ")
            if line == "":
                break
            try:
                sock.send(_parse(line))
            except ValueError:
                print("  invalid hex value")
            except OSError as err:
                print(f"  send failed: {err}")
                break
    except (EOFError, KeyboardInterrupt):
        pass
    stop.set()
    sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
