#!/usr/bin/env bash
# Installa il bridge Cast su Raspberry Pi OS (Bookworm).
# Eseguire come root:  sudo ./install-pi.sh 192.168.178.124
set -euo pipefail

TARGET="${1:-192.168.178.124}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "[*] target: $TARGET"
id -u | grep -q '^0$' || { echo "eseguire con sudo"; exit 1; }

command -v avahi-daemon >/dev/null || {
  echo "[*] installo avahi-daemon"
  apt-get update -qq && apt-get install -y avahi-daemon >/dev/null
}

install -d /opt/castbridge
install -m 0755 "$HERE/castbridge.py" /opt/castbridge/castbridge.py

echo "[*] avahi service"
install -m 0644 "$HERE/avahi-googlecast.service" /etc/avahi/services/googlecast.service

echo "[*] systemd unit"
sed "s/192\.168\.178\.124/$TARGET/" "$HERE/castbridge.service" \
  > /etc/systemd/system/castbridge.service

systemctl daemon-reload
systemctl enable --now castbridge.service
systemctl restart avahi-daemon

echo "[+] fatto. Verifica con:"
echo "    avahi-browse -rt _googlecast._tcp"
echo "    journalctl -u castbridge -f"
