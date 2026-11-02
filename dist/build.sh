#!/usr/bin/env bash
# Ricostruisce il binario librespot per aarch64 Linux (musl, statico).
# Richiede Docker su host arm64 (Apple Silicon o Linux aarch64).
set -euo pipefail
cd "$(dirname "$0")/.."

FEATURES="rustls-tls-webpki-roots,with-libmdns"

docker run --rm -v "$PWD/dist:/out" rust:1-alpine sh -c \
  "apk add --no-cache musl-dev >/dev/null && \
   cargo install librespot --locked --no-default-features \
     --features $FEATURES --root /out"

BIN=dist/bin/librespot
if command -v sha256sum >/dev/null; then
  sha256sum "$BIN" > dist/librespot-aarch64.sha256
else
  shasum -a 256 "$BIN" > dist/librespot-aarch64.sha256
fi

echo "[+] $BIN"
file "$BIN" 2>/dev/null || true
cat dist/librespot-aarch64.sha256
