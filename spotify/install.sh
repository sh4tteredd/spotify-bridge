#!/usr/bin/env bash
# Installa i servizi Spotify -> Nest Hub su Debian/DietPi con systemd.
# Uso: sudo ./install.sh [IP_DEL_NEST_HUB]
set -euo pipefail

TARGET="${1:-192.168.178.124}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${DEST:-/opt/spotify-bridge}"

[ "$(id -u)" -eq 0 ] || { echo "eseguire con sudo"; exit 1; }

echo "[*] device: $TARGET"

# 1. copia il progetto (se non è già lì)
if [ "$HERE" != "$DEST/spotify" ]; then
  mkdir -p "$DEST"
  cp -a "$HERE/../." "$DEST/"
fi

# 2. dipendenze
command -v ffmpeg >/dev/null || { echo "[*] installo ffmpeg"; apt-get update -qq; apt-get install -y ffmpeg; }
python3 -c "import sys" >/dev/null

# 3. cache credenziali persistente
install -d -m 0750 /var/cache/librespot

# 4. unit systemd
sed "s/192\.168\.178\.124/$TARGET/g" "$DEST/spotify/spotify-cast.service" \
  > /etc/systemd/system/spotify-cast.service
install -m 0644 "$DEST/spotify/librespot.service" /etc/systemd/system/librespot.service

systemctl daemon-reload
systemctl enable --now spotify-cast.service

if [ -x /usr/local/bin/librespot ]; then
  systemctl enable --now librespot.service
else
  echo
  echo "[!] /usr/local/bin/librespot assente: compila o copia il binario, poi:"
  echo "    systemctl enable --now librespot.service"
fi

echo
echo "[+] fatto."
echo "    systemctl status spotify-cast"
echo "    journalctl -u spotify-cast -f"
