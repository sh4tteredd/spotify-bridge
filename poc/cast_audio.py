#!/usr/bin/env python3
"""
Cast a local audio file to a Google Nest Hub over CASTV2 (port 8009).

Flow: CONNECT -> LAUNCH Default Media Receiver (CC1AD845) -> CONNECT to the
app transport -> LOAD media -> read MEDIA_STATUS.

Usage: python3 cast_audio.py <device-ip> <audio-url> [content-type] [title]
"""
import json
import os
import socket
import ssl
import struct
import sys
import time

CONNECTION_NS = "urn:x-cast:com.google.cast.tp.connection"
HEARTBEAT_NS = "urn:x-cast:com.google.cast.tp.heartbeat"
RECEIVER_NS = "urn:x-cast:com.google.cast.receiver"
MEDIA_NS = "urn:x-cast:com.google.cast.media"
SOURCE_ID = "sender-0"
RECEIVER_ID = "receiver-0"
DEFAULT_MEDIA_RECEIVER = "CC1AD845"


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


def encode(destination: str, namespace: str, payload: dict) -> bytes:
    body = (
        _varint_field(1, 0)
        + _str_field(2, SOURCE_ID)
        + _str_field(3, destination)
        + _str_field(4, namespace)
        + _varint_field(5, 0)
        + _str_field(6, json.dumps(payload))
    )
    return struct.pack(">I", len(body)) + body


def decode(data: bytes) -> dict:
    i, fields = 0, {}
    while i < len(data):
        key, shift = 0, 0
        while True:
            b = data[i]; i += 1
            key |= (b & 0x7F) << shift; shift += 7
            if not (b & 0x80):
                break
        field, wire = key >> 3, key & 0x7
        if wire == 0:
            val, shift = 0, 0
            while True:
                b = data[i]; i += 1
                val |= (b & 0x7F) << shift; shift += 7
                if not (b & 0x80):
                    break
            fields[field] = val
        elif wire == 2:
            ln, shift = 0, 0
            while True:
                b = data[i]; i += 1
                ln |= (b & 0x7F) << shift; shift += 7
                if not (b & 0x80):
                    break
            fields[field] = data[i:i + ln]; i += ln
        else:
            raise ValueError(f"wire type {wire}")
    try:
        return json.loads(fields.get(6, b"").decode("utf-8"))
    except Exception:
        return {"_raw": fields.get(6, b"").decode("utf-8", "replace")}


class CastClient:
    def __init__(self, host: str, port: int = 8009):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection((host, port), timeout=10)
        self.sock = ctx.wrap_socket(raw, server_hostname=host)
        self.sock.settimeout(10)

    def send(self, dest: str, ns: str, payload: dict) -> None:
        self.sock.sendall(encode(dest, ns, payload))

    def recv(self) -> dict:
        hdr = b""
        while len(hdr) < 4:
            c = self.sock.recv(4 - len(hdr))
            if not c:
                raise ConnectionError("socket closed")
            hdr += c
        (ln,) = struct.unpack(">I", hdr)
        body = b""
        while len(body) < ln:
            c = self.sock.recv(ln - len(body))
            if not c:
                raise ConnectionError("closed mid-frame")
            body += c
        msg = decode(body)
        if os.environ.get("CAST_DEBUG"):
            print(f"    << {json.dumps(msg)[:600]}")
        return msg

    def wait_for(self, msg_type: str, timeout: float = 15.0, predicate=None) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.sock.settimeout(max(0.5, deadline - time.time()))
            msg = self.recv()
            if msg.get("type") == "PING":
                self.send("receiver-0", HEARTBEAT_NS, {"type": "PONG"})
                continue
            if msg.get("type") == msg_type and (predicate is None or predicate(msg)):
                return msg
        raise TimeoutError(f"no {msg_type} within {timeout}s")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    host, url = sys.argv[1], sys.argv[2]
    ctype = sys.argv[3] if len(sys.argv) > 3 else "audio/wav"
    title = sys.argv[4] if len(sys.argv) > 4 else "Test audio"

    c = CastClient(host)
    print("[+] TLS ok")

    c.send(RECEIVER_ID, CONNECTION_NS, {"type": "CONNECT"})
    c.send(RECEIVER_ID, RECEIVER_NS,
           {"type": "SET_VOLUME", "volume": {"level": 0.5}, "requestId": 1})
    c.send(RECEIVER_ID, RECEIVER_NS,
           {"type": "LAUNCH", "appId": DEFAULT_MEDIA_RECEIVER, "requestId": 2})

    status = c.wait_for(
        "RECEIVER_STATUS", 25,
        predicate=lambda m: any(a.get("appId") == DEFAULT_MEDIA_RECEIVER
                                for a in m.get("status", {}).get("applications", [])),
    )
    apps = status.get("status", {}).get("applications", [])
    target = next((a for a in apps if a.get("appId") == DEFAULT_MEDIA_RECEIVER), None)
    if not target:
        print(f"[-] receiver non avviato: {json.dumps(status)[:400]}")
        return 1
    transport = target["transportId"]
    print(f"[+] receiver avviato, transportId={transport}")

    c.send(transport, CONNECTION_NS, {"type": "CONNECT"})
    c.send(transport, MEDIA_NS, {
        "type": "LOAD",
        "requestId": 3,
        "media": {
            "contentId": url,
            "streamType": "BUFFERED",
            "contentType": ctype,
            "metadata": {"type": 0, "metadataType": 0, "title": title},
        },
        "autoplay": True,
    })

    deadline = time.time() + 40
    last = None
    ok = False
    while time.time() < deadline:
        c.send(transport, MEDIA_NS, {"type": "GET_STATUS", "requestId": 4})
        try:
            media = c.wait_for("MEDIA_STATUS", 6)
        except TimeoutError:
            continue
        st = media.get("status", [{}])[0] if media.get("status") else None
        if not st:
            if last != ("empty",):
                print("[*] MEDIA_STATUS vuoto")
                last = ("empty",)
            time.sleep(0.5)
            continue
        ext = st.get("extendedStatus", {}).get("playerState")
        state = (st.get("playerState"), ext)
        if state != last:
            print(f"[*] playerState={st.get('playerState')} "
                  f"extended={ext} idleReason={st.get('idleReason')}")
            last = state
        err = st.get("detailedError")
        if err:
            print(f"[-] errore: {json.dumps(err)}")
            break
        if st.get("playerState") == "PLAYING":
            ok = True
            break
        if st.get("idleReason") in ("ERROR", "CANCELLED", "INTERRUPTED"):
            break
        time.sleep(1)

    if ok:
        time.sleep(2)
        c.send(transport, MEDIA_NS, {"type": "GET_STATUS", "requestId": 5})
        sts = c.wait_for("MEDIA_STATUS", 10).get("status") or [{}]
        st2 = sts[0]
        print(f"[+] after 2s: state={st2.get('playerState')} "
              f"currentTime={st2.get('currentTime', 0):.1f}s")
        print("[+] AUDIO IN RIPRODUZIONE sul dispositivo")
        return 0

    print("[-] riproduzione non confermata")
    return 1


if __name__ == "__main__":
    sys.exit(main())
