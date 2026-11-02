# spotify-bridge

Far suonare **Spotify** su un **Google Nest Hub** che non si fa scoprire e che
non ha il receiver Spotify.

Due problemi indipendenti, due soluzioni indipendenti:

| Problema | Sintomo | Soluzione |
|---|---|---|
| Il device non ha responder mDNS | nessuna app lo vede tra i dispositivi Cast | `bridge/` — annuncio mDNS + relay TCP |
| Il device rifiuta l'app Spotify | `LAUNCH_ERROR NOT_FOUND` su `CC32E753` | `spotify/` — Spotify Connect + HLS verso il Default Media Receiver |

```
                    ┌──────────────────────────────────────────────┐
   Spotify app ────►│ librespot ("Cucina Audio")                   │
                    │   PCM ─► FIFO ─► ffmpeg ─► HLS (:8090)       │
                    └───────────────────────────────┬──────────────┘
                                                    │ CASTV2 LOAD
                                                    ▼
   App Cast  ────►  bridge (mDNS + relay :8009) ──► Nest Hub (:8009)
   (Netflix, YouTube, Plex)                         (192.168.178.124)
```

## Requisiti

- `python3` (solo standard library)
- `ffmpeg` con encoder AAC
- `librespot` (per la parte Spotify)
- **Spotify Premium** per il flusso Connect

## Avvio rapido (Spotify)

```bash
cd spotify
./run.sh 192.168.178.124
# poi in Spotify seleziona "Cucina Audio"
```

## Struttura

```
bridge/     annuncio _googlecast._tcp + relay TCP verso il device
spotify/    Spotify Connect -> PCM -> HLS -> Cast
poc/        strumenti di analisi e verifica (Cast v2, mDNS, API di setup)
```

| File | Cosa fa |
|---|---|
| `bridge/castbridge.py` | responder mDNS + relay 8008/8009/8443, con modalità `--inspect` |
| `bridge/avahi-googlecast.service` | equivalente per Raspberry Pi / Linux con Avahi |
| `bridge/castbridge.service` | unit systemd del relay |
| `bridge/install-pi.sh` | installazione su Raspberry Pi OS |
| `spotify/spotify_cast.py` | PCM → HLS → cast, con iniezione di silenzio |
| `spotify/fifo_writer.sh` | wrapper per il backend `subprocess` di librespot |
| `spotify/run.sh` | avvia l'intera pipeline |
| `poc/cast_audio.py` | cast di un file audio (usato come libreria) |
| `poc/cast_monitor.py` | monitor dello stato del device |
| `poc/cast_probe_apps.py` | quali receiver il device accetta |
| `poc/castv2_probe.py` | connessione CASTV2 e `GET_STATUS` |
| `poc/discovery_probe.py` | chi risponde a SSDP/mDNS sulla LAN |
| `poc/eureka_tls.py` | lettura dell'API di setup su `:8443` |

## Note tecniche

- Il device non annuncia `_googlecast._tcp`: `bridge/` sintetizza l'annuncio.
- Il device non ha il receiver Spotify: `spotify/` evita del tutto Cast per
  Spotify e usa il Default Media Receiver (`CC1AD845`), che funziona.
- Il backend `pipe` di librespot scrive su stdout e ci mescola i messaggi: usare
  `subprocess` con un wrapper.
- Latenza attesa: 4–6 s.

Ogni cartella ha il proprio README con dettagli, problemi incontrati e comandi
di verifica.

## Stato

Verificato su Google Nest Hub, firmware `1.56.332325`, da macOS (Apple Silicon).
La pipeline Spotify è stata confermata funzionante con audio reale; il bridge
Cast è stato verificato con un sender di test.
