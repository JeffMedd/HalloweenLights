#!/usr/bin/env python3
"""Listen for DDP packets and summarise them.

Useful for proving the Pi is sending what you think it is before any hardware
exists, and for checking pixel counts match the WLED configuration.

    python3 tools/ddp_listen.py --port 4048 --seconds 5 --pixels-per-section 52
"""

from __future__ import annotations

import argparse
import socket
import time


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4048)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--pixels-per-section", type=int, default=52)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    sock.settimeout(0.5)

    started = time.monotonic()
    packets = 0
    total_bytes = 0
    senders: dict[str, int] = {}
    last_report = started

    print(f"Listening on {args.host}:{args.port} for {args.seconds}s")
    while time.monotonic() - started < args.seconds:
        try:
            data, addr = sock.recvfrom(2048)
        except socket.timeout:
            continue
        packets += 1
        total_bytes += len(data)
        senders[addr[0]] = senders.get(addr[0], 0) + 1

        if len(data) < 10:
            print("  short packet, ignoring")
            continue
        flags, sequence, dtype, output = data[0], data[1], data[2], data[3]
        offset = int.from_bytes(data[4:8], "big")
        length = int.from_bytes(data[8:10], "big")
        payload = data[10:10 + length]
        channels = 4 if dtype == 0x1B else 3
        pixels = len(payload) // channels

        now = time.monotonic()
        if args.verbose or now - last_report >= 1.0:
            last_report = now
            summary = []
            for index in range(0, pixels, max(1, args.pixels_per_section)):
                chunk = payload[index * channels:(index + args.pixels_per_section) * channels]
                if not chunk:
                    continue
                r = sum(chunk[0::channels]) // max(1, len(chunk) // channels)
                g = sum(chunk[1::channels]) // max(1, len(chunk) // channels)
                b = sum(chunk[2::channels]) // max(1, len(chunk) // channels)
                summary.append(f"#{r:02x}{g:02x}{b:02x}")
            print(f"  {addr[0]} seq={sequence:2d} push={bool(flags & 1)} "
                  f"type=0x{dtype:02X} out={output} off={offset} "
                  f"{pixels}px  " + " ".join(summary))

    elapsed = time.monotonic() - started
    print(f"\n{packets} packets, {total_bytes} bytes in {elapsed:.1f}s "
          f"({packets / max(elapsed, 0.001):.1f} pkt/s)")
    for host, count in senders.items():
        print(f"  from {host}: {count}")


if __name__ == "__main__":
    main()
