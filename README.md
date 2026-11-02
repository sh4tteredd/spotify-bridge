# spotify-bridge

Far suonare **Spotify** e **AirPlay** su un **Google Nest Hub** che non si fa
scoprire e che non ha il receiver Spotify.

## Architettura

Un core indipendente dalla sorgente e due pacchetti sorgente, mutuamente
esclusivi: non serve — e non è possibile — tenerli attivi insieme.

```
                 ┌──────────────────────────────┐
  librespot ────►│                              │
  (Spotify)      │   core/audiobridge           │
                 │   PCM -> ffmpeg -> HLS       │──CASTV2──► Nest Hub
  shairport ────►│   watchdog sulla sessione    │            (CC1AD845)
  (AirPlay)      └──────────────────────────────┘
```

| Cartella | Ruolo |
|---|---|
| `core/` | PCM → HLS → Cast. Unico motore, ~300 righe, nessuna dipendenza dalla sorgente |
| `sources/airplay/` | shairport-sync 5.x, backend `stdout` |
| `sources/spotify/` | librespot, backend `subprocess` |
| `bridge/` | annuncio mDNS + relay Cast (per far ricomparire il device nelle app) |
| `dist/` | librespot precompilato per aarch64 (musl, statico) |
| `poc/` | strumenti di analisi e verifica |

## Installazione

```bash
# AirPlay
sudo sources/airplay/install.sh 192.168.178.124

# oppure Spotify (serve /usr/local/bin/librespot, vedi dist/)
sudo sources/spotify/install.sh 192.168.178.124
```

Ogni `install.sh` installa anche il core.

## Cambiare sorgente

Le due unit hanno `Conflicts=` reciproco: avviarne una ferma l'altra. Per
rendere la scelta persistente al boot:

```bash
sudo systemctl disable --now spotify-source
sudo systemctl enable --now airplay-source
```

## Perché due pacchetti

Il core è identico per entrambe le sorgenti: duplicarlo significherebbe
correggere ogni bug due volte. La separazione è quindi tra **sorgenti**, non nel
motore. `sources/spotify` e `sources/airplay` sono pacchetti installabili in modo
indipendente e non possono collidere, grazie a `Conflicts=`.

## Requisiti

- `python3` (solo standard library)
- `ffmpeg` con encoder AAC
- `shairport-sync` (AirPlay) oppure `librespot` (Spotify)
- Spotify Premium per la sorgente Spotify

## Latenza

~10 s con HLS (2 s AirPlay + ~6 s di segmenti + buffer del device). Il limite è
strutturale nel trasporto HLS; per scendere a ~5-6 s servirebbe uno stream MP3
progressivo al posto dei segmenti.

## Stato

Verificato su Google Nest Hub, firmware `1.56.332325`, DietPi aarch64 su Pi 3.
