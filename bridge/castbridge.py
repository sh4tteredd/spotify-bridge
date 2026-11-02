#!/usr/bin/env python3
"""
castbridge -- advertise a Google Cast device that does not advertise itself,
              and relay the Cast control channel to it.

Why: the Nest Hub at CAST_TARGET answers on :8009 but has no mDNS responder
(UDP 5353 -> ICMP port unreachable). Senders (Spotify, YouTube, Chrome) only
find devices through _googlecast._tcp, so they never see it.

This daemon:
  1. publishes _googlecast._tcp over mDNS with a synthesized TXT record
     (name/model/serial taken from the real device),
  2. listens on TCP 8008/8009/8443 and forwards verbatim to the real device.

The TLS session on 8009 is end-to-end (senders do not verify the self-signed
device certificate), so the bridge never has to understand CastMessage.

Usage:
  python3 castbridge.py --target 192.168.178.124 --name "Cucina Display"
"""
import argparse
import os
import socket
import ssl
import struct
import threading
import time

MDNS_ADDR = "224.0.0.251"
MDNS_PORT = 5353
SERVICE = "_googlecast._tcp.local"
RELAY_PORTS = (8008, 8009, 8443)


# --------------------------------------------------------------------------- #
# DNS wire helpers
# --------------------------------------------------------------------------- #
def encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        out += bytes([len(label)]) + label.encode("utf-8")
    return out + b"\x00"


def rr(name: str, rtype: int, rclass: int, ttl: int, rdata: bytes) -> bytes:
    return (
        encode_name(name)
        + struct.pack(">HHIH", rtype, rclass, ttl, len(rdata))
        + rdata
    )


def read_name(buf: bytes, off: int) -> tuple[str, int]:
    labels = []
    while True:
        ln = buf[off]
        off += 1
        if ln == 0:
            break
        if ln & 0xC0 == 0xC0:          # compression pointer
            ptr = struct.unpack(">H", buf[off - 1:off + 1])[0] & 0x3FFF
            sub, _ = read_name(buf, ptr)
            labels.append(sub)
            return ".".join(labels), off + 1
        labels.append(buf[off:off + ln].decode("utf-8", "replace"))
        off += ln
    return ".".join(labels), off


# --------------------------------------------------------------------------- #
# mDNS responder
# --------------------------------------------------------------------------- #
class MdnsResponder:
    def __init__(self, bind_ip: str, host: str, instance: str, txt: list[str],
                 port: int = 8009, verbose: bool = False):
        self.bind_ip = bind_ip
        self.host = host
        self.instance = instance
        self.fqdn = f"{instance}.{SERVICE}"
        self.txt = txt
        self.port = port
        self.verbose = verbose

    def _records(self) -> list[bytes]:
        return [
            rr(SERVICE, 12, 0x0001, 4500, encode_name(self.fqdn)),          # PTR
            rr(self.fqdn, 33, 0x8001, 120,                                    # SRV
               struct.pack(">HHH", 0, 0, self.port) + encode_name(self.host)),
            rr(self.fqdn, 16, 0x8001, 4500, self._txt_rdata()),               # TXT
            rr(self.host, 1, 0x8001, 120, socket.inet_aton(self.bind_ip)),    # A
        ]

    def _txt_rdata(self) -> bytes:
        out = b""
        for item in self.txt:
            raw = item.encode("utf-8")
            out += bytes([len(raw)]) + raw
        return out or b"\x00"

    def _response(self, answers: list[bytes]) -> bytes:
        header = struct.pack(">HHHHHH", 0, 0x8400, 0, len(answers), 0, 0)
        return header + b"".join(answers)

    def _announcement(self) -> bytes:
        return self._response(self._records())

    def _handle_query(self, data: bytes, addr) -> None:
        try:
            _, _, qdcount, _, _, _ = struct.unpack(">HHHHHH", data[:12])
        except struct.error:
            return
        off, names = 12, []
        for _ in range(qdcount):
            name, off = read_name(data, off)
            names.append(name.lower())
            off += 4  # qtype + qclass
        if not names:
            return
        wanted = {SERVICE, self.fqdn, self.host, "_services._dns-sd._udp.local"}
        if not wanted & set(names):
            return
        if self.verbose:
            print(f"    [mdns] query {names} from {addr[0]}")
        # answer the query and announce: covers clients that asked via unicast
        pkt = self._announcement()
        self.sock.sendto(pkt, (MDNS_ADDR, MDNS_PORT))

    def run(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        try:
            self.sock.bind(("", MDNS_PORT))
        except OSError:
            self.sock.bind((self.bind_ip, MDNS_PORT))
        try:
            mreq = socket.inet_aton(MDNS_ADDR) + socket.inet_aton(self.bind_ip)
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError as exc:
            print(f"[!] join multicast: {exc}")
        self.sock.settimeout(1.0)

        pkt = self._announcement()
        for delay in (0, 1, 3):
            time.sleep(delay)
            self.sock.sendto(pkt, (MDNS_ADDR, MDNS_PORT))
        print(f"[+] mDNS: {self.fqdn} -> {self.host} ({self.bind_ip}:{self.port})")

        last_announce = time.time()
        while True:
            try:
                data, addr = self.sock.recvfrom(9000)
            except socket.timeout:
                data = None
            except OSError:
                continue
            if data and (data[2] & 0x80) == 0:      # QR=0 -> query
                self._handle_query(data, addr)
            if time.time() - last_announce > 30:
                self.sock.sendto(pkt, (MDNS_ADDR, MDNS_PORT))
                last_announce = time.time()


# --------------------------------------------------------------------------- #
# TCP relay
# --------------------------------------------------------------------------- #
def pipe(src: socket.socket, dst: socket.socket, tag: str) -> None:
    try:
        while True:
            chunk = src.recv(65536)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


def _decode_cast(data: bytes) -> dict:
    """Pull namespace (field 4) and UTF-8 payload (field 6) out of a CastMessage."""
    i, fields = 0, {}
    try:
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
                return {}
    except (IndexError, ValueError):
        return {}
    ns = fields.get(4, b"").decode("utf-8", "replace")
    payload = fields.get(6, b"")
    return {"ns": ns, "payload": payload.decode("utf-8", "replace"),
            "binary": len(fields.get(7, b""))}


def _read_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def _relay_frames(src, dst, tag: str) -> None:
    """Frame-aware relay: logs each CastMessage, forwards the exact bytes."""
    try:
        while True:
            hdr = _read_exact(src, 4)
            (ln,) = struct.unpack(">I", hdr)
            body = _read_exact(src, ln)
            dst.sendall(hdr + body)
            info = _decode_cast(body)
            if info:
                payload = info["payload"].replace("\n", " ")[:400]
                extra = f" [+{info['binary']}B bin]" if info["binary"] else ""
                print(f"    {tag} {info['ns']} {payload}{extra}", flush=True)
    except (ConnectionError, OSError):
        pass
    finally:
        for s in (src, dst):
            try:
                s.close()
            except OSError:
                pass


def serve_tls_inspect(bind_ip: str, port: int, target: str,
                      cert: str, key: str, verbose: bool) -> None:
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((bind_ip, port))
    ls.listen(16)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(cert, key)
    cctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cctx.check_hostname = False
    cctx.verify_mode = ssl.CERT_NONE
    print(f"[+] inspect {bind_ip}:{port} -> {target}:{port} (TLS terminato)")
    while True:
        client, addr = ls.accept()
        try:
            c = sctx.wrap_socket(client, server_side=True)
            u = cctx.wrap_socket(
                socket.create_connection((target, port), timeout=10),
                server_hostname=target)
        except (ssl.SSLError, OSError) as exc:
            print(f"[!] handshake con {addr[0]} fallito: {exc}", flush=True)
            client.close()
            continue
        print(f"--- sessione {addr[0]}:{addr[1]} ---", flush=True)
        threading.Thread(target=_relay_frames, args=(c, u, "C->D"),
                         daemon=True).start()
        threading.Thread(target=_relay_frames, args=(u, c, "D->C"),
                         daemon=True).start()


def serve_port(bind_ip: str, port: int, target: str, tport: int,
               verbose: bool) -> None:
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((bind_ip, port))
    ls.listen(16)
    print(f"[+] relay {bind_ip}:{port} -> {target}:{tport}")
    while True:
        client, addr = ls.accept()
        try:
            upstream = socket.create_connection((target, tport), timeout=10)
        except OSError as exc:
            print(f"[!] {addr[0]} -> {target}:{tport} failed: {exc}")
            client.close()
            continue
        if verbose:
            print(f"    [{port}] {addr[0]}:{addr[1]} connesso")
        threading.Thread(target=pipe, args=(client, upstream, f"{port}a"),
                         daemon=True).start()
        threading.Thread(target=pipe, args=(upstream, client, f"{port}b"),
                         daemon=True).start()


def detect_ip(target: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target, 8009))
        return s.getsockname()[0]
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="IP of the real Cast device")
    ap.add_argument("--name", default="Cucina Display", help="friendly name (fn)")
    ap.add_argument("--model", default="Google Nest Hub", help="model (md)")
    ap.add_argument("--serial", default="FA8FCA779A57", help="bs / cast serial")
    ap.add_argument("--id", default="b36c4e25d986c66848451124a6fc27a9",
                    help="32 hex chars, cast id")
    ap.add_argument("--bind", default=None, help="bridge IP (auto-detected)")
    ap.add_argument("--no-mdns", action="store_true",
                    help="do not advertise; use avahi-daemon instead (Linux/Pi)")
    ap.add_argument("--inspect", action="store_true",
                    help="terminate TLS on 8009 and log CastMessage traffic")
    ap.add_argument("--cert", default="cert.pem")
    ap.add_argument("--key", default="key.pem")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    bind_ip = args.bind or detect_ip(args.target)
    host = "nesthub-bridge.local"
    instance = f"Google-Nest-Hub-{args.id[:16]}"
    txt = [
        f"id={args.id}",
        f"cd={args.id.upper()}",
        "rm=",
        "ve=05",
        f"md={args.model}",
        "ic=/setup/icon.png",
        f"fn={args.name}",
        "ca=199172",
        "st=0",
        f"bs={args.serial}",
        "nf=1",
        "rs=",
    ]

    print(f"[*] bridge su {bind_ip}, target {args.target}")
    print(f"[*] nome annunciato: {args.name} ({args.model})")

    if args.no_mdns:
        print("[*] mDNS delegato ad avahi-daemon")
    else:
        threading.Thread(
            target=MdnsResponder(bind_ip, host, instance, txt, 8009,
                                 args.verbose).run,
            daemon=True,
        ).start()

    if args.inspect:
        import subprocess
        if not (os.path.exists(args.cert) and os.path.exists(args.key)):
            print("[*] genero certificato self-signed per il bridge")
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                 "-keyout", args.key, "-out", args.cert, "-days", "365",
                 "-subj", "/CN=castbridge"],
                check=True, capture_output=True)
        threading.Thread(
            target=serve_tls_inspect,
            args=(bind_ip, 8009, args.target, args.cert, args.key,
                  args.verbose),
            daemon=True).start()
        for port in (8008, 8443):
            threading.Thread(target=serve_port,
                             args=(bind_ip, port, args.target, port,
                                   args.verbose),
                             daemon=True).start()
    else:
        for port in RELAY_PORTS:
            threading.Thread(target=serve_port,
                             args=(bind_ip, port, args.target, port,
                                   args.verbose),
                             daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
