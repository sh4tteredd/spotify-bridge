#!/usr/bin/env bash
# Installa il pacchetto SORGENTE Spotify + il core audio bridge.
# Uso: sudo ./install.sh [IP_DEL_NEST_HUB]
set -euo pipefail

TARGET="${1:-192.168.178.124}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "eseguire con sudo"; exit 1; }

[ -x /usr/local/bin/librespot ] || {
  echo "[!] /usr/local/bin/librespot assente."
  echo "    Copia il binario da dist/bin/librespot, oppure compila con cargo."
  exit 1
}

echo "[*] core audio bridge"
install -d /opt/audiobridge
install -m 0755 "$ROOT/core/audiobridge.py" /opt/audiobridge/audiobridge.py
install -m 0644 "$ROOT/poc/cast_audio.py" /opt/audiobridge/cast_audio.py
install -m 0755 "$HERE/fifo_writer.sh" /opt/audiobridge/fifo_writer.sh

cat > /etc/default/audiobridge <<EOF
DEVICE=$TARGET
FIFO=/run/audiobridge/pcm
PORT=8090
TITLE=Spotify
EOF

install -m 0644 "$ROOT/core/audiobridge.service" /etc/systemd/system/audiobridge.service
install -m 0644 "$HERE/spotify-source.service" /etc/systemd/system/spotify-source.service

install -d -m 0750 /var/cache/librespot

echo "[*] pulizia delle vecchie unit"
systemctl disable --now spotify-cast.service airplay-bridge.service 2>/dev/null || true
rm -f /etc/systemd/system/spotify-cast.service \
      /etc/systemd/system/airplay-bridge.service

systemctl daemon-reload
systemctl disable airplay-source.service 2>/dev/null || true
systemctl enable audiobridge.service
systemctl enable --now spotify-source.service
systemctl restart audiobridge.service
systemctl restart spotify-source.service

echo
echo "[+] Spotify attivo. In Spotify scegli 'Cucina Audio'."
echo "    Per passare ad AirPlay:  sudo systemctl disable --now spotify-source"
echo "                             sudo systemctl enable --now airplay-source"
