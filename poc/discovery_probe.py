#!/usr/bin/env python3
"""
Multicast discovery probe: SSDP (1900) and mDNS (5353).

Sends an M-SEARCH and an mDNS PTR query (QU bit set) and prints the source IP
of every responder. Used to tell apart "the device does not advertise" from
"multicast does not reach the device".

Usage: python3 discovery_probe.py [seconds]
"""
import socket
import struct
import sys
import time

SSDP_ADDR = "239.255.255.250"
MDNS_ADDR = "224.0.0.251"


def ssdp(seconds: float) -> dict:
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR}:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        "ST: ssdp:all\r\n\r\n"
    ).encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.settimeout(0.5)
    s.sendto(msg, (SSDP_ADDR, 1900))
    found = {}
    end = time.time() + seconds
    while time.time() < end:
        try:
            data, addr = s.recvfrom(4096)
        except socket.timeout:
            continue
        text = data.decode("utf-8", "replace")
        server = next((l for l in text.splitlines() if l.lower().startswith("server:")), "")
        found.setdefault(addr[0], set()).add(server.strip())
    s.close()
    return found


def mdns(seconds: float, svc: str = "_googlecast._tcp.local") -> dict:
    # PTR question, QU bit set (unicast reply)
    qname = b""
    for label in svc.encode().split(b"."):
        if label:
            qname += bytes([len(label)]) + label
    qname += b"\x00"
    pkt = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + qname + struct.pack(">HH", 12, 0x8001)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 0))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.settimeout(0.5)
    s.sendto(pkt, (MDNS_ADDR, 5353))
    found = {}
    end = time.time() + seconds
    while time.time() < end:
        try:
            data, addr = s.recvfrom(9000)
        except socket.timeout:
            continue
        found[addr[0]] = len(data)
    s.close()
    return found


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
    print("== SSDP multicast responders ==")
    for ip, servers in sorted(ssdp(seconds).items()):
        print(f"  {ip:<16} {', '.join(sorted(servers))}")
    print("== mDNS _googlecast._tcp responders ==")
    for ip, ln in sorted(mdns(seconds).items()):
        print(f"  {ip:<16} {ln} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
