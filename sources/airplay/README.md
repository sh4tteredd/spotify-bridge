# sources/airplay — shairport-sync

Pacchetto sorgente AirPlay: installa il core e `airplay-source.service`, che
esegue shairport-sync con backend `stdout` e redirige il PCM nella FIFO del core.

## Installazione

```bash
sudo ./install.sh 192.168.178.124
```

Cosa fa:

1. copia il core in `/opt/audiobridge`;
2. scrive `/etc/default/audiobridge` con `TITLE=AirPlay`;
3. installa e configura `shairport-sync` (se manca) con:
   - `name = "Cucina AirPlay"`
   - `output_backend = "stdout"`
4. disabilita le vecchie unit;
5. abilita `audiobridge` + `airplay-source`.

Poi, dal telefono o dal Mac, scegli **Cucina AirPlay** nel menu AirPlay.

## Caratteristiche

| | |
|---|---|
| AirPlay | **classico (v1)** — niente multi-room, niente AirPlay 2 |
| Formato | PCM S16LE 44.1 kHz stereo |
| Latenza tipica | **~10 s** (2 s AirPlay + ~6 s HLS + buffer del device) |
| Requisiti | nessuno (nessun account) |
| Sorgenti | iPhone, iPad, Mac, Apple Music, Spotify via AirPlay, YouTube, audio di sistema |

## Xrun / ritardi

Se l'audio torna a scatti, il primo sospetto è il relay del core, non
shairport-sync: verifica che `core/audiobridge.py` usi la versione con
`select()`. Il secondo è il Wi-Fi: due flussi simultanei sul Pi 3 soffrono.

## Confronto

`Conflicts=spotify-source.service`: avviare questo ferma Spotify e viceversa.
