# Spotify → Nest Hub

Il Nest Hub `192.168.178.124` **non ha il receiver Spotify** (`CC32E753`):
qualsiasi sender che scopre il device via Cast riceve `LAUNCH_ERROR NOT_FOUND`.
Questa pipeline aggira il problema: Spotify suona su "Cucina Audio" (Spotify
Connect), l'audio viene riconvertito in HLS e il Nest Hub lo riproduce col
Default Media Receiver, che invece funziona.

## Schema

```
Spotify app ──Connect──> librespot ──PCM S16──> FIFO /tmp/librespot.pcm
                                                     │
                                      spotify_cast.py│ (inietta silenzio quando
                                                     │  librespot è in pausa)
                                                     ▼
                                                  ffmpeg ──> HLS (2 s per segmento,
                                                     │        finestra 20 s)
                                          HTTP :8090 │
                                                     ▼
                                          CASTV2 LOAD (LIVE, CC1AD845)
```

Il Pi (o il Mac) è l'unico che produce audio; il device riceve solo lo stream
HTTP. Latenza tipica 4–6 s.

## Avvio (macOS)

```bash
# 1. pipeline (crea la FIFO, avvia ffmpeg, casta il flusso)
python3 spotify_cast.py --device 192.168.178.124 --fifo /tmp/librespot.pcm

# 2. sorgente Spotify Connect
librespot -n "Cucina Audio" \
          -B subprocess -d "$PWD/fifo_writer.sh" \
          -b 320 --cache /tmp/librespot-cache
```

Poi in Spotify seleziona **Cucina Audio**.

## Installazione come servizio (DietPi / Raspberry Pi OS)

```bash
sudo ./install.sh 192.168.178.124
```

Installa `/opt/spotify-bridge`, crea `/var/cache/librespot`, abilita
`spotify-cast.service` e `librespot.service` (quest'ultimo solo se
`/usr/local/bin/librespot` esiste).

Ordine di avvio garantito: `librespot` ha `Requires=` e `After=` su
`spotify-cast`, perché la FIFO deve avere un lettore prima che la sorgente
apra lo scrittore (altrimenti il `cat` resta bloccato).

## Perché questi flag

| Flag | Motivo |
|---|---|
| `-B subprocess -d fifo_writer.sh` | il backend `subprocess` **non usa una shell**: `cat > file` inline fallisce con `cat: >: No such file`. Serve uno script senza metacaratteri. |
| `-b 320` | qualità sorgente; il ricodifica HLS è a 160 kbps AAC |
| `--cache` | conserva le credenziali OAuth tra i riavvii |
| `--volume-ctrl fixed` (opzionale) | se il volume Spotify basso rende l'audio impercettibile: manda PCM a fondo scala e lascia il volume al Nest Hub |

## Problemi incontrati

| Sintomo | Causa | Soluzione |
|---|---|---|
| `cat: >: No such file or directory`, stream silenzioso | `subprocess` non interpreta `>` | wrapper `fifo_writer.sh` |
| Schermo nero, `BUFFERING` | finestra HLS 8 s troppo corta | `-hls_list_size 10` |
| URL OAuth finiva nel PCM | `-B pipe` scrive PCM e messaggi su stdout | backend `subprocess` |
| Volume quasi impercettibile | mixer log di librespot a volume 19% ≈ -66 dB | alzare il volume, oppure `-E linear` / `-E fixed` |
| Sessione persa a ogni riavvio | librespot ricrea la sessione Connect | riselezionare "Cucina Audio" |

## Verifica

```bash
# il device sta riproducendo?
python3 ../poc/cast_monitor.py 192.168.178.124 30

# c'è audio nei segmenti? (2632 byte = silenzio, ~48 KB = audio)
LAST=$(ls -t /tmp/spotify-hls/*.ts | head -1)
ffmpeg -hide_banner -i "$LAST" -af volumedetect -f null - 2>&1 | grep volume
```

## Note

- Serve **Spotify Premium**: Connect streaming non funziona con account free.
- Il bridge Cast (`../bridge/`) resta utile per le altre app (Netflix, YouTube,
  Plex) ma **non** per Spotify: quella voce dà `NOT_FOUND`.
- Non c'è sincronizzazione video: solo musica.
