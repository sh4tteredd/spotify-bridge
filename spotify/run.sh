#!/usr/bin/env bash
# Avvia l'intera pipeline Spotify -> Nest Hub.
# Uso: ./run.sh [IP_DEVICE]
set -euo pipefail
cd "$(dirname "$0")"

DEVICE="${1:-192.168.178.124}"
FIFO="${FIFO:-/tmp/librespot.pcm}"
PORT="${PORT:-8090}"

cleanup() {
  echo
  echo "[*] stop"
  kill "${LR_PID:-}" "${SC_PID:-}" 2>/dev/null || true
  pkill -f fifo_writer.sh 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[*] device $DEVICE"
python3 -u spotify_cast.py --device "$DEVICE" --fifo "$FIFO" \
        --http-port "$PORT" --title "Spotify" &
SC_PID=$!

# aspetta che il cast sia attivo prima di avviare la sorgente
for _ in $(seq 1 30); do
  grep -q "PLAYING" /dev/null 2>/dev/null || true
  sleep 1
  if [ -n "${SC_PID:-}" ] && ! kill -0 "$SC_PID" 2>/dev/null; then
    echo "[-] spotify_cast è terminato"; exit 1
  fi
  [ "$_" -ge 10 ] && break
done

# credenziali: al primo avvio completare il flusso OAuth stampato da librespot
librespot -n "Cucina Audio" -B subprocess -d "$PWD/fifo_writer.sh" \
          -b 320 --cache /tmp/librespot-cache &
LR_PID=$!

echo
echo "[+] pronto. In Spotify seleziona 'Cucina Audio'."
echo "    Ctrl-C per fermare tutto."
wait
