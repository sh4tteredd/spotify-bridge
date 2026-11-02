#!/usr/bin/env python3
"""
Minimal CASTV2 client (stdlib only, no protobuf dependency).

Connects to port 8009/tcp (TLS, self-signed cert) and performs a read-only
GET_STATUS on the receiver namespace. Demonstrates that the local Cast control
channel requires no pairing, credential or user consent beyond being on the
same L2/L3 network.

Usage: python3 castv2_probe.py 192.168.178.124
"""
import json
import socket
import ssl
import struct
import sys

CONNECTION_NS = "urn:x-cast:com.google.cast.tp.connection"
HEARTBEAT_NS = "urn:x-cast:com.google.cast.tp.heartbeat"
RECEIVER_NS = "urn:x-cast:com.google.cast.receiver"
SOURCE_ID = "sender-0"
RECEIVER_ID = "receiver-0"


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _str_field(field: int, value: str) -> bytes:
    raw = value.encode("utf-8")
    return _varint((field << 3) | 2) + _varint(len(raw)) + raw


def _varint_field(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def encode_cast_message(destination: str, namespace: str, payload: str,
                        source: str = SOURCE_ID) -> bytes:
    body = (
        _varint_field(1, 0)                       # protocol_version = CASTV2_1_0
        + _str_field(2, source)                   # source_id
        + _str_field(3, destination)              # destination_id
        + _str_field(4, namespace)                # namespace
        + _varint_field(5, 0)                     # payload_type = STRING
        + _str_field(6, payload)                  # payload_utf8
    )
    return struct.pack(">I", len(body)) + body


def decode_cast_message(data: bytes) -> dict:
    """Just enough protobuf to pull the UTF-8 payload out."""
    i, fields = 0, {}
    while i < len(data):
        key, shift, k = 0, 0, 0
        while True:
            b = data[i]
            i += 1
            key |= (b & 0x7F) << shift
            shift += 7
            k = b
            if not (k & 0x80):
                break
        field, wire = key >> 3, key & 0x7
        if wire == 0:
            val, shift = 0, 0
            while True:
                b = data[i]
                i += 1
                val |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break
            fields[field] = val
        elif wire == 2:
            ln, shift = 0, 0
            while True:
                b = data[i]
                i += 1
                ln |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break
            fields[field] = data[i:i + ln]
            i += ln
        else:
            raise ValueError(f"unsupported wire type {wire}")
    payload = fields.get(6, b"")
    try:
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return {"_raw": payload}


def recv_message(sock) -> dict:
    header = b""
    while len(header) < 4:
        chunk = sock.recv(4 - len(header))
        if not chunk:
            raise ConnectionError("socket closed")
        header += chunk
    (length,) = struct.unpack(">I", header)
    body = b""
    while len(body) < length:
        chunk = sock.recv(length - len(body))
        if not chunk:
            raise ConnectionError("socket closed mid-frame")
        body += chunk
    return decode_cast_message(body)


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.178.124"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8009

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_ciphers("DEFAULT@SECLEVEL=0")

    raw = socket.create_connection((host, port), timeout=10)
    sock = ctx.wrap_socket(raw, server_hostname=host)
    print(f"[+] TLS established with {host}:{port} -> {sock.version()} {sock.cipher()[0]}")

    sock.sendall(encode_cast_message(RECEIVER_ID, CONNECTION_NS, '{"type":"CONNECT"}'))
    sock.sendall(encode_cast_message(RECEIVER_ID, RECEIVER_NS,
                                     '{"type":"GET_STATUS","requestId":1}'))

    status = None
    for _ in range(10):
        msg = recv_message(sock)
        if msg.get("type") == "RECEIVER_STATUS":
            status = msg
            break

    if not status:
        print("[-] no RECEIVER_STATUS received")
        return 1

    print("[+] RECEIVER_STATUS (unauthenticated):")
    print(json.dumps(status, indent=2, ensure_ascii=False))
    sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
