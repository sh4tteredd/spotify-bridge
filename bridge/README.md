# Cast bridge — far tornare visibile il Nest Hub

Il Nest Hub `192.168.178.124` non ha un responder mDNS attivo (UDP 5353 →
ICMP port unreachable), quindi nessuna app lo trova in `_googlecast._tcp`.
Questo bridge lo rende di nuovo scopribile **senza toccare il dispositivo**.

## Come funziona

```
Sender (Spotify/Chrome)          Raspberry Pi                  Nest Hub
        │                             │                            │
        │ 1. mDNS _googlecast._tcp    │                            │
        │────────────────────────────>│                            │
        │ 2. TLS su Pi:8009           │                            │
        │────────────────────────────>│ 3. passthrough TCP         │
        │                             │───────────────────────────>│
        │                             │      CastMessage (TLS)     │
        │<────────────────────────────┼────────────────────────────│
        │                             │                            │
        │ 4. l'audio arriva dalla CDN direttamente al device         │
```

Il TLS è end-to-end: il Pi **non decodifica** il protocollo, fa solo
inoltro di byte. Funziona perché i sender non verificano il certificato
self-signed del device.

## Test già eseguito (sul Mac, stessa LAN)

```
[+] mDNS: Google-Nest-Hub-b36c4e25d986c668._googlecast._tcp.local
          -> nesthub-bridge.local (192.168.178.127:8009)
[+] relay 192.168.178.127:8008 -> 192.168.178.124:8008
[+] relay 192.168.178.127:8009 -> 192.168.178.124:8009
[+] relay 192.168.178.127:8443 -> 192.168.178.124:8443
```

`dns-sd -B _googlecast._tcp` ha mostrato la nuova istanza, e un sender di test
connesso al bridge ha avviato il receiver sul device reale:

```
[+] receiver avviato, transportId=0c8b08a2-...
[*] playerState=BUFFERING
[*] playerState=PLAYING
[+] AUDIO IN RIPRODUZIONE sul dispositivo
```

Il device ha anche riportato `statusText: "Trasmissione: Test via bridge"`.

## Uso rapido (macOS/Linux, senza avahi)

```bash
python3 castbridge.py --target 192.168.178.124 --name "Cucina Display"
```

## Installazione sul Raspberry Pi

```bash
scp -r bridge pi@raspberrypi.local:~/
ssh pi@raspberrypi.local 'cd ~/bridge && sudo ./install-pi.sh 192.168.178.124'
```

Lo script installa:

| File | Destinazione |
|---|---|
| `castbridge.py` | `/opt/castbridge/castbridge.py` |
| `avahi-googlecast.service` | `/etc/avahi/services/googlecast.service` |
| `castbridge.service` | `/etc/systemd/system/castbridge.service` |

Verifica:

```bash
avahi-browse -rt _googlecast._tcp
systemctl status castbridge
```

## Parametri

| Flag | Default | Nota |
|---|---|---|
| `--target` | — | IP del Nest Hub |
| `--name` | `Cucina Display` | `fn` nel TXT (nome mostrato nelle app) |
| `--model` | `Google Nest Hub` | `md` |
| `--serial` | `FA8FCA779A57` | `bs`, dal CN del certificato Cast |
| `--id` | `b36c4e25...` | 32 hex, derivato dall'UUID DIAL |
| `--no-mdns` | off | usa avahi invece del responder integrato |

## Limiti noti da verificare con Spotify

1. **`id=` sintetico.** Il vero cast id del device non è osservabile (non
   annuncia nulla). Abbiamo derivato 32 hex dall'UUID DIAL. Alcune app possono
   confrontarlo con il cloud: se Spotify non compare, è il primo sospetto.
2. **Il device risponde col proprio IP** in alcuni messaggi. I sender più
   rigidi potrebbero notare la differenza tra l'IP annunciato e quello che
   risponde.
3. **Doppia istanza.** Se un giorno l'mDNS del device torna (dopo un reboot),
   vedrai due voci. Disattiva il bridge o il service Avahi.
4. **Solo controllo, non audio.** Il Pi inoltra i comandi; lo stream arriva
   dalla CDN al device. Se ti serve che l'audio passi davvero dal Pi, serve
   l'architettura C (librespot/icecast + cast by IP).

## Rollback

```bash
sudo systemctl disable --now castbridge
sudo rm /etc/avahi/services/googlecast.service /etc/systemd/system/castbridge.service
sudo systemctl restart avahi-daemon
```
