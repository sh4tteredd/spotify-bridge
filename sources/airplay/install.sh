#!/usr/bin/env bash
# Installa il pacchetto SORGENTE AirPlay + il core audio bridge.
# Uso: sudo ./install.sh [IP_DEL_NEST_HUB]
set -euo pipefail

TARGET="${1:-192.168.178.124}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "eseguire con sudo"; exit 1; }

echo "[*] core audio bridge"
install -d /opt/audiobridge
install -m 0755 "$ROOT/core/audiobridge.py" /opt/audiobridge/audiobridge.py
install -m 0644 "$ROOT/poc/cast_audio.py" /opt/audiobridge/cast_audio.py

cat > /etc/default/audiobridge <<EOF
DEVICE=$TARGET
FIFO=/run/audiobridge/pcm
PORT=8090
TITLE=AirPlay
TRANSPORT=hls
EOF

install -m 0644 "$ROOT/core/audiobridge.service" /etc/systemd/system/audiobridge.service
install -m 0644 "$HERE/airplay-source.service" /etc/systemd/system/airplay-source.service

echo "[*] shairport-sync"
command -v shairport-sync >/dev/null || {
  apt-get update -qq && apt-get install -y shairport-sync
}
python3 "$HERE/patch_conf.py"

echo "[*] pulizia delle vecchie unit"
systemctl disable --now spotify-cast.service airplay-bridge.service \
         shairport-sync.service 2>/dev/null || true
rm -f /etc/systemd/system/spotify-cast.service \
      /etc/systemd/system/airplay-bridge.service

systemctl daemon-reload
systemctl disable spotify-source.service 2>/dev/null || true
systemctl enable audiobridge.service
systemctl enable --now airplay-source.service
systemctl restart audiobridge.service
systemctl restart airplay-source.service

echo
echo "[+] AirPlay attivo. In Spotify/AirPlay scegli 'Cucina AirPlay'."
echo "    Per passare a Spotify:  sudo systemctl disable --now airplay-source"
echo "                            sudo systemctl enable --now spotify-source"
